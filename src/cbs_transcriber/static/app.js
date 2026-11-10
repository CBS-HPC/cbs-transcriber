"use strict";

const $ = (selector) => document.querySelector(selector);
// Release profile (ADR 0145): the server marks hidden features on <html> before any script
// runs. Hidden features never offer new work; existing results, exports and recovery
// controls stay. Static elements carry data-feature; dynamic UI asks featureOn().
const RELEASE = {
  profile: document.documentElement.dataset.profile || "full",
  label: document.documentElement.dataset.releaseLabel || "",
  off: new Set(
    (document.documentElement.dataset.off || "").split(" ").filter(Boolean),
  ),
};
function featureOn(name) {
  return !RELEASE.off.has(name);
}
function applyReleaseProfile(root = document) {
  for (const element of root.querySelectorAll("[data-feature]")) {
    if (element.dataset.feature.split(" ").every(featureOn)) continue;
    // CSS keeps marked elements hidden even when later code shows them again. A select
    // cannot reliably hide an option, so it is removed and can never be chosen.
    if (element instanceof HTMLOptionElement) element.remove();
    else element.hidden = true;
  }
}
applyReleaseProfile();
if (RELEASE.profile !== "full" || RELEASE.label) {
  $("#release-note").textContent =
    `This version: ${RELEASE.label || RELEASE.profile.toUpperCase()} (release profile ${RELEASE.profile}). Please mention it in bug reports.`;
  $("#release-note").hidden = false;
}
if (!featureOn("ucloud_enhancement"))
  $("#speaker-tag").title =
    "Detect who speaks when for the whole recording on this computer. A new result is saved; this one is kept.";
// Automatic routes by length to this computer or UCloud; without UCloud processing,
// one-click work runs here.
function processingPlace() {
  return featureOn("ucloud_enhancement") ? "auto" : "local";
}
function processingWhere(plural = false) {
  return featureOn("ucloud_enhancement")
    ? `${plural ? "They run" : "It runs"} on this computer or on UCloud, chosen by length (Automatic).`
    : `${plural ? "They run" : "It runs"} on this computer.`;
}
const OPENAI_URL = "https://api.openai.com/v1";
let presets = {};
function configurationLabel(name, model) {
  const preset = Object.values(presets).find(
    (p) =>
      (!model || p.model === model) &&
      [
        p.name,
        p.name.replace(/^OpenAI /, ""),
        p.legacyName,
        p.model,
        `OpenAI · ${p.model}`,
      ].includes(name),
  );
  const label = (preset?.name || name || model)?.replace(
    /^UCloud Transcriber(?= |$)/u,
    "UCloud",
  );
  const runtime =
    state.providers?.some(
      (provider) =>
        provider.provider_type === "ucloud" &&
        provider.model === model &&
        provider.name === name,
    ) &&
    state.modelPresets?.ucloud_cbs_runtime?.models?.find(
      (entry) => entry.model === model && entry.fixed_language === "da",
    );
  return runtime && label && !/\(Danish\)$/iu.test(label)
    ? `${label} (Danish)`
    : label;
}
function speakerName(record, id) {
  return record?.speaker_defaults?.[id] ?? record?.speakers?.[id] ?? id;
}
const h = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const state = {
  projects: [],
  project: null,
  recordings: [],
  recording: null,
  selected: new Set(),
  jobs: [],
  mode: "free",
  media: "audio",
  dirty: false,
  editing: null,
  replayEnd: null,
  polling: false,
  providers: [],
  defaultProvider: null,
  settingsProvider: null,
  settingsTab: "openai",
  protectedDefaults: {},
  catalog: null,
  comparison: null,
  transcriptionFeatures: {},
};
let viewEpoch = 0,
  editVersion = 0,
  mediaHandoffGeneration = 0,
  mediaHandoff = null;
function cancelMediaHandoff() {
  mediaHandoffGeneration++;
  mediaHandoff?.next.pause();
  mediaHandoff?.cancel?.();
  mediaHandoff = null;
}
function recordingContext() {
  return {
    epoch: viewEpoch,
    id: state.recording?.id,
    result: state.recording?.active_transcript_id,
    editVersion,
  };
}
function sameRecordingView(context) {
  return (
    context.epoch === viewEpoch &&
    context.id === state.recording?.id &&
    context.result === state.recording?.active_transcript_id &&
    context.editVersion === editVersion
  );
}
function acceptRecording(context, recording) {
  if (
    !sameRecordingView(context) ||
    recording.revision < state.recording.revision
  )
    return false;
  // Notes save independently of transcript revisions.
  if ((state.recording.notes_revision || 0) > (recording.notes_revision || 0)) {
    recording.notes = state.recording.notes;
    recording.notes_revision = state.recording.notes_revision;
  }
  if (
    (state.recording.language_revision || 0) >
    (recording.language_revision || 0)
  ) {
    recording.language_override = state.recording.language_override;
    recording.language_revision = state.recording.language_revision;
  }
  state.recording = recording;
  queueMicrotask(() => {
    if (typeof renderManualReviews === "function") renderManualReviews();
  });
  return true;
}
let nameResolver = null,
  wordContext = null,
  editorReplayId = null,
  timedWordSaving = false,
  passageTextSaving = false,
  passageRealignChoice = null,
  toastTimer = null,
  activePassage = null,
  activeWord = null,
  activeComparePassage = null,
  activeCompareWord = null;
const pendingRecordingRenames = new Set();
const automaticTranscriptScrolls = new WeakSet();

async function loadProviders() {
  const [data, catalog, modelPresets] = await Promise.all([
    api("/providers"),
    state.catalog || api("/providers/catalog"),
    api("/providers/model-presets"),
  ]);
  state.catalog = catalog;
  state.modelPresets = modelPresets;
  const presetChoice = $("#provider-preset").value;
  const nativeChoice = $("#native-model-choice").value;
  const hadPresets = $("#provider-preset").options.length > 0;
  const hadNativeModels = $("#native-model-choice").options.length > 0;
  presets = Object.fromEntries(
    modelPresets.openai.models.map((m) => [m.key, m]),
  );
  $("#provider-preset").innerHTML = modelPresets.openai.models
    .map((m) => `<option value="${h(m.key)}">${h(m.name)}</option>`)
    .join("");
  const knownModels = new Set(modelPresets.openai.models.map((m) => m.model));
  for (const provider of data.providers) {
    if (providerTab(provider) !== "openai" || knownModels.has(provider.model))
      continue;
    knownModels.add(provider.model);
    const option = document.createElement("option");
    option.value = `existing-model-${provider.id}`;
    option.dataset.existingModel = provider.model;
    option.dataset.capability = provider.capability;
    option.dataset.name = provider.name;
    option.textContent = `Existing model: ${provider.model}`;
    $("#provider-preset").append(option);
  }
  if (hadPresets) $("#provider-preset").value = presetChoice;
  $("#native-model-choice").innerHTML = modelPresets.ucloud_transcriber.models
    .map(
      (m) =>
        `<option value="${h(m.model)}">${h(m.name)} · ~${h(m.memory_gb)} GB</option>`,
    )
    .join("");
  if (hadNativeModels) $("#native-model-choice").value = nativeChoice;
  state.protectedDefaults = data.protected_defaults;
  state.providers = data.providers;
  state.defaultProvider = data.default_provider_id;
  renderProviderSelects();
}
function visibleProvider(provider) {
  return ["openai", "ucloud"].includes(providerTab(provider));
}
// Hidden CBS Runtime models stay saved, but are never offered for new work.
function offeredProvider(provider) {
  return (
    visibleProvider(provider) &&
    (featureOn("cbs_runtime") ||
      ucloudModelGroup(provider) !== "CBS Runtime / WhisperX")
  );
}
function allowedProviders() {
  return state.providers.filter(
    (p) =>
      offeredProvider(p) &&
      p.enabled !== false &&
      (!state.project ||
        state.project.classification === "standard" ||
        p.approved_for?.includes(state.project.classification)),
  );
}
function providerOptions(
  selected,
  providers = allowedProviders(),
  forSettings = false,
) {
  if (!providers.length)
    return `<option value="">${state.providers.some((provider) => offeredProvider(provider) && provider.enabled !== false) ? "No approved providers" : "No enabled models"}</option>`;
  const option = (p) =>
    `<option value="${p.id}" ${p.id === selected ? "selected" : ""} ${!forSettings && p.provider_type === "ucloud-vllm" && (!p.base_url || !p.has_key) ? "disabled" : ""}>${h(forSettings ? configurationLabel(p.name, p.model) : modelLabel(p))}${p.provider_type === "ucloud-vllm" && !p.base_url ? " · awaiting deployment" : ""}</option>`;
  return providers.every((p) => providerTab(p) === "ucloud")
    ? ucloudModelGroups(providers, option)
    : providers.map(option).join("");
}
function ucloudModelGroup(model) {
  const preset = ucloudModels().find(
    (p) => p.model === model.model || p.aliases?.includes(model.model),
  );
  return (model.engine || preset?.engine) === "whisperx"
    ? "CBS Runtime / WhisperX"
    : "UCloud Transcriber";
}
function ucloudModels() {
  return [
    ...(state.modelPresets?.ucloud_transcriber.models || []),
    ...(state.modelPresets?.ucloud_cbs_runtime?.models || []),
  ];
}
function ucloudGroupLabels() {
  return featureOn("cbs_runtime")
    ? ["UCloud Transcriber", "CBS Runtime / WhisperX"]
    : ["UCloud Transcriber"];
}
function ucloudModelGroups(models, render) {
  return ucloudGroupLabels()
    .map((label) => {
      const entries = models.filter(
        (model) => ucloudModelGroup(model) === label,
      );
      return entries.length
        ? `<optgroup label="${label}">${entries.map(render).join("")}</optgroup>`
        : "";
    })
    .join("");
}
function modelLabel(provider) {
  const kind = providerTab(provider);
  if (kind === "openai") {
    const preset = Object.values(presets).find(
      (p) => p.model === provider.model,
    );
    return preset ? preset.name.replace(/^OpenAI[ ·]*/, "") : provider.model;
  }
  if (kind === "ucloud") {
    const preset = ucloudModels().find(
      (p) => p.model === provider.model || p.aliases?.includes(provider.model),
    );
    return preset
      ? preset.name.replace(/^UCloud( Transcriber)?[ ·]*/, "")
      : provider.model;
  }
  const name = configurationLabel(provider.name, provider.model);
  return name.includes(provider.model) ? name : `${name} · ${provider.model}`;
}
function providerKindOptions(selected) {
  const labels = {
    openai: "OpenAI",
    ucloud: "UCloud",
  };
  const kinds = [...new Set(allowedProviders().map(providerTab))];
  if (!kinds.length)
    return `<option value="">${state.providers.some((provider) => offeredProvider(provider) && provider.enabled !== false) ? "No approved providers" : "No enabled models"}</option>`;
  return kinds
    .map(
      (kind) =>
        `<option value="${kind}" ${kind === selected ? "selected" : ""}>${labels[kind]}</option>`,
    )
    .join("");
}
function selectedProvider(selected) {
  return (
    allowedProviders().find((p) => p.id === selected) || allowedProviders()[0]
  );
}
function renderProviderPair(kindSelector, modelSelector, selected) {
  const provider = selectedProvider(selected);
  const kind = provider ? providerTab(provider) : "";
  $(kindSelector).innerHTML = providerKindOptions(kind);
  $(modelSelector).innerHTML = providerOptions(
    selected,
    allowedProviders().filter((p) => providerTab(p) === kind),
  );
}
document.addEventListener("change", (event) => {
  const kind = event.target.closest("[data-provider-kind]");
  if (!kind) return;
  const scope = kind.dataset.providerKind;
  const models =
    scope === "selected" ? $("#bulk-provider") : $("#file-provider");
  models.innerHTML = providerOptions(
    null,
    allowedProviders().filter((p) => providerTab(p) === kind.value),
  );
  models.dispatchEvent(new Event("change", { bubbles: true }));
});
function renderProviderSelects() {
  renderProviderPair(
    "#bulk-provider-kind",
    "#bulk-provider",
    state.defaultProvider,
  );
  renderProviderPair(
    "#file-provider-kind",
    "#file-provider",
    state.recording?.provider_id || state.defaultProvider,
  );
  renderTranscriptionFeatures();
  $("#settings-provider").innerHTML =
    '<option value="">New configuration</option>' +
    providerOptions(
      state.settingsProvider,
      state.providers.filter((p) => providerTab(p) === state.settingsTab),
      true,
    );
}
function nativeFeatures(providerId) {
  const capability = state.providers.find(
    (p) => p.id === providerId,
  )?.capability;
  return {
    word_timing: ["words", "words_speakers"].includes(capability),
    speaker_labels: ["speakers", "words_speakers"].includes(capability),
  };
}
function transcriptionFeatures(scope, providerId) {
  const native = nativeFeatures(providerId);
  const requested = state.transcriptionFeatures[scope] || {};
  return Object.fromEntries(
    Object.keys(native).map((key) => [key, native[key] || !!requested[key]]),
  );
}
function featureChoices(scope, providerId, name) {
  const native = nativeFeatures(providerId);
  const selected = transcriptionFeatures(scope, providerId);
  return `<div class="transcription-features" role="group" aria-label="Transcription features for ${h(name)}">${Object.entries(
    { word_timing: "Word timing", speaker_labels: "Speaker labels" },
  )
    .map(
      ([key, label]) =>
        `<label title="${native[key] ? "Included in the selected model" : whisperxProvider(providerId) ? "Detected in the same UCloud WhisperX run" : featureOn("ucloud_enhancement") ? "Add after transcription using Local or UCloud processing" : "Add after transcription on this computer"}"><input type="checkbox" data-transcription-feature="${key}" data-feature-scope="${h(scope)}" ${selected[key] ? "checked" : ""} ${native[key] ? "disabled" : ""}>${label}${native[key] ? '<span class="feature-included" aria-label="included in model"> · included</span>' : ""}</label>`,
    )
    .join("")}</div>`;
}
function renderTranscriptionFeatures() {
  $("#bulk-features").innerHTML = featureChoices(
    "selected",
    $("#bulk-provider").value,
    "selected recordings",
  );
  if (state.recording)
    $("#file-features").innerHTML = featureChoices(
      state.recording.id,
      $("#file-provider").value,
      "this recording",
    );
}
document.addEventListener("change", (event) => {
  const input = event.target.closest("[data-transcription-feature]");
  if (!input) return;
  const scope = input.dataset.featureScope;
  const requested = (state.transcriptionFeatures[scope] ||= {});
  requested[input.dataset.transcriptionFeature] = input.checked;
  const providerId =
    scope === "selected"
      ? $("#bulk-provider").value
      : $("#file-provider").value;
  if (state.providers.find((p) => p.id === providerId)?.capability === "json") {
    // Untimed text needs alignment before speakers can be assigned to its words.
    if (
      input.dataset.transcriptionFeature === "speaker_labels" &&
      input.checked
    )
      requested.word_timing = true;
    if (input.dataset.transcriptionFeature === "word_timing" && !input.checked)
      requested.speaker_labels = false;
  }
  renderTranscriptionFeatures();
  if (!state.recording) renderRecordings();
});
$("#bulk-provider").addEventListener(
  "change",
  handle(async (event) => {
    const previous = state.defaultProvider;
    const value = event.target.value;
    const controls = [$("#bulk-provider-kind"), $("#bulk-provider")];
    controls.forEach((control) => {
      control.disabled = true;
    });
    state.defaultProvider = value;
    renderTranscriptionFeatures();
    renderRecordings();
    try {
      await api("/providers/default", {
        method: "PUT",
        body: { provider_id: value },
      });
    } catch (error) {
      if (state.defaultProvider === value) {
        state.defaultProvider = previous;
        renderProviderSelects();
        renderRecordings();
      }
      throw error;
    } finally {
      controls.forEach((control) => {
        control.disabled = false;
      });
    }
  }),
);
$("#file-provider").addEventListener(
  "change",
  handle((event) =>
    chooseRecordingProvider(state.recording.id, event.target.value),
  ),
);
async function chooseRecordingProvider(identifier, value) {
  const recording =
    state.recording?.id === identifier
      ? state.recording
      : state.recordings.find((r) => r.id === identifier);
  const previous = recording.provider_id;
  recording.provider_id = value;
  renderTranscriptionFeatures();
  if (!state.recording) renderRecordings();
  try {
    await api(`/recordings/${identifier}/provider`, {
      method: "PATCH",
      body: { provider_id: value },
    });
  } catch (error) {
    if (recording.provider_id === value) recording.provider_id = previous;
    renderProviderSelects();
    if (!state.recording) renderRecordings();
    throw error;
  }
}

// A busy cursor after a short delay tells the researcher a click is being handled.
let pendingRequests = 0,
  busyTimer = null;
function trackBusy(delta) {
  pendingRequests += delta;
  clearTimeout(busyTimer);
  if (pendingRequests > 0)
    busyTimer = setTimeout(
      () => document.documentElement.classList.add("busy"),
      250,
    );
  else document.documentElement.classList.remove("busy");
}
async function api(path, options = {}) {
  if (!options.background) {
    trackBusy(1);
    try {
      return await request(path, options);
    } finally {
      trackBusy(-1);
    }
  }
  return request(path, options);
}
let pendingWorkingEditChoice = null;
function manualTranscriptEdit(path, options) {
  const method = (options.method || "GET").toUpperCase();
  return (
    (method === "PATCH" &&
      /^\/recordings\/[^/]+\/(?:segments\/[^/]+(?:\/(?:speaker|edit-v[123]))?|speakers)$/u.test(
        path,
      )) ||
    (method === "POST" &&
      /^\/recordings\/[^/]+\/(?:segments\/[^/]+\/(?:merge|undo-merge|undo-split|split(?:-v2)?|delete|restore)|transcripts\/[^/]+\/(?:merge|conflicts|wording-adopt|versions\/[^/]+\/restore))$/u.test(
        path,
      ))
  );
}
function manualEditTarget(path, method, body = {}) {
  // A passage editor's explicit Working choice covers its text and word-timing
  // tabs; both save to the same revision-fenced passage.
  const mode =
    method === "PATCH" &&
    /^\/recordings\/[^/]+\/segments\/[^/]+(?:\/edit-v[123])?$/u.test(path) &&
    ["word", "mapped_word", "timed_word"].includes(body.mode)
      ? "free"
      : body.mode;
  return JSON.stringify([
    method,
    path.replace(/(\/segments\/[^/]+)\/edit-v[123]$/u, "$1"),
    mode ?? null,
    body.speaker ?? null,
    body.direction ?? null,
    body.fingerprint ?? null,
    body.delete_version_id ?? null,
  ]);
}
async function workingEditChoice(target, fresh = false, preview = null) {
  if (fresh) pendingWorkingEditChoice = null;
  // A refreshed merge/conflict preview may be newer than the editor behind it.
  // Bind its choice separately; changing the editor head alone would bless stale drafts.
  const context = recordingContext(),
    visible = state.recording,
    recording = preview?.recording || visible,
    visibleRevision = visible?.revision,
    visibleRoleRevision = visible?.result_roles?.revision || 0,
    visibleHead = visible?.active_version?.id,
    revision = recording?.revision,
    roleRevision = recording?.result_roles?.revision || 0,
    head = recording?.active_version?.id,
    working = recording?.result_roles?.working;
  if (!context.id || !context.result || !head)
    throw new Error(
      "Reload this recording before editing its Working version.",
    );
  const current = () =>
    sameRecordingView(context) &&
    state.recording.revision === visibleRevision &&
    (state.recording.result_roles?.revision || 0) === visibleRoleRevision &&
    state.recording.active_version?.id === visibleHead &&
    (!preview || preview.current());
  if (
    recording.id !== context.id ||
    recording.active_transcript_id !== context.result ||
    !current()
  )
    throw new Error(
      "The reviewed preview changed. Refresh it before applying changes.",
    );
  if (
    pendingWorkingEditChoice?.id === context.id &&
    pendingWorkingEditChoice?.result === context.result &&
    pendingWorkingEditChoice?.epoch === context.epoch &&
    pendingWorkingEditChoice?.target === target &&
    pendingWorkingEditChoice?.revision === revision &&
    pendingWorkingEditChoice?.roleRevision === roleRevision &&
    pendingWorkingEditChoice?.head === head &&
    pendingWorkingEditChoice?.preview === preview
  )
    return pendingWorkingEditChoice.choice;
  let makeWorking = working?.version_id === head;
  if (!makeWorking) {
    const dialog = $("#working-edit-dialog");
    if (dialog.open)
      throw new Error("Finish the Working version choice first.");
    $("#working-edit-message").textContent = working
      ? "This result is not the current Working version. Set your edited version as Working when saved, or keep the current Working version?"
      : "No Working version is selected. Set your edited version as Working when saved, or leave Working unassigned?";
    dialog.querySelector('[value="keep"]').textContent = working
      ? "Keep current Working"
      : "Leave unassigned";
    const answer = await new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue), {
        once: true,
      });
      dialog.returnValue = "cancel";
      dialog.showModal();
    });
    if (answer === "cancel" || !answer) return null;
    if (!current())
      throw new Error(
        "The recording or Working version changed. Your edit was not saved; try again.",
      );
    makeWorking = answer === "set";
  }
  const choice = {
    version_id: head,
    role_revision: roleRevision,
    make_working: makeWorking,
  };
  pendingWorkingEditChoice = {
    id: context.id,
    result: context.result,
    epoch: context.epoch,
    target,
    revision,
    roleRevision,
    head,
    preview,
    choice,
  };
  return choice;
}
$("#working-edit-dialog").addEventListener("click", (event) => {
  const button = event.target.closest("[data-working-edit-choice]");
  if (button) event.currentTarget.close(button.value);
});
async function request(path, options = {}) {
  requireSettledLanguage(path, options);
  const { workingPreview, ...transportOptions } = options;
  options = transportOptions;
  const editing = manualTranscriptEdit(path, options),
    editContext = editing ? recordingContext() : null,
    editRevision = editing ? state.recording?.revision : null,
    target = editing
      ? manualEditTarget(
          path,
          (options.method || "GET").toUpperCase(),
          options.body,
        )
      : null;
  if (
    workingPreview &&
    (!editing ||
      !/^\/recordings\/[^/]+\/transcripts\/[^/]+\/(?:merge|conflicts)$/u.test(
        path,
      ) ||
      options.body?.revision !== workingPreview.recording?.revision)
  )
    throw new Error(
      "The reviewed preview changed. Refresh it before applying changes.",
    );
  if (editing) {
    const choice = await workingEditChoice(target, false, workingPreview);
    if (!choice) throw new Error("Edit cancelled; no changes were saved.");
    if (
      workingPreview &&
      (!workingPreview.current() ||
        !sameRecordingView(editContext) ||
        state.recording.revision !== editRevision)
    )
      throw new Error(
        "The reviewed preview changed. Refresh it before applying changes.",
      );
    requireSettledLanguage(path, options);
    options = { ...options, body: { ...options.body, working_choice: choice } };
  }
  const headers = {
    "X-CBS-Request": "1",
    ...CBSLibraries.instanceHeaders(),
    ...options.headers,
  };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    if (typeof options.body !== "string")
      options.body = JSON.stringify(options.body);
  }
  const response = await fetch(`/api${path}`, { ...options, headers });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    CBSLibraries.stale(error);
    const detail = error.detail;
    const failure = new Error(
      typeof detail === "string"
        ? detail
        : typeof detail?.message === "string"
          ? detail.message
          : "The request could not be completed.",
    );
    failure.status = response.status;
    failure.detail = detail;
    if (
      editing &&
      [400, 409].includes(response.status) &&
      sameRecordingView(editContext)
    ) {
      // A different window can change only the Working role while this draft is open.
      // Refresh that role without replacing the editor contents, then ask again on retry.
      try {
        const latest = await api(`/recordings/${editContext.id}`, {
          background: true,
        });
        if (
          sameRecordingView(editContext) &&
          state.recording.revision === editRevision &&
          latest.revision === editRevision &&
          latest.active_transcript_id === editContext.result &&
          latest.active_version?.id === state.recording.active_version?.id &&
          latest.result_roles?.revision !==
            state.recording.result_roles?.revision
        ) {
          state.recording.result_roles = latest.result_roles;
          pendingWorkingEditChoice = null;
          renderResults();
          failure.message +=
            " Your correction is retained; save again to review the Working choice.";
        }
      } catch {
        // Keep the original error and the unsaved draft if refresh is unavailable.
      }
    }
    // Some refusals carry a code the caller can act on (e.g. prepare_runtime).
    if (typeof detail?.code === "string") failure.code = detail.code;
    throw failure;
  }
  return response.json();
}
const pendingDeletions = new Map();
async function deleteManaged(path, current, progress) {
  if (pendingDeletions.has(path)) return false;
  pendingDeletions.set(path, "Stopping jobs before deletion…");
  let operation = null,
    shared = false,
    fingerprint = null;
  try {
    while (current()) {
      progress(pendingDeletions.get(path));
      const query = operation
        ? `?operation_id=${encodeURIComponent(operation)}${shared ? "&confirm_shared=true" : ""}${fingerprint ? `&fingerprint=${encodeURIComponent(fingerprint)}` : ""}`
        : "";
      let reply;
      try {
        reply = await api(path + query, { method: "DELETE", background: true });
      } catch (error) {
        if (!current()) return false;
        const detail = error.detail;
        if (
          error.status !== 409 ||
          !["confirm_shared_cancellation", "confirm_remote_cleanup"].includes(
            detail?.code,
          ) ||
          !/^[a-f0-9]{32}$/.test(detail.operation_id)
        )
          throw error;
        const names = (detail.affected_recordings || [])
          .map((recording) => recording.name)
          .join(", ");
        const folders = (detail.remote_folders || [])
          .map(
            (folder) =>
              `${folder.server} · ${folder.folder}${folder.action === "retain" ? " (kept for other recordings)" : " → UCloud Trash"}`,
          )
          .join("\n");
        if (
          !confirm(
            `${detail.message}\n${names}${folders ? `\n\n${folders}` : ""}\n\nContinue deleting?`,
          )
        )
          return false;
        operation = detail.operation_id;
        shared = detail.code === "confirm_shared_cancellation" || shared;
        if (/^[a-f0-9]{64}$/.test(detail.fingerprint))
          fingerprint = detail.fingerprint;
        continue;
      }
      if (!current()) return false;
      if (reply.ok === true) {
        if (reply.local_cleanup) {
          await pollJobs();
          $("#activity").hidden = false;
        }
        return true;
      }
      if (
        !["cancelling", "releasing"].includes(reply.status) ||
        !/^[a-f0-9]{32}$/.test(reply.operation_id)
      )
        throw new Error(
          "Deletion was not confirmed. The recording list has been kept.",
        );
      operation = reply.operation_id;
      pendingDeletions.set(path, reply.message || "Waiting for jobs to stop…");
      progress(pendingDeletions.get(path));
      // Only this open view continues a deletion; navigation/reload leaves its data intact.
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    return false;
  } finally {
    pendingDeletions.delete(path);
  }
}
function toast(message, error = false) {
  clearTimeout(toastTimer);
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.hidden = false;
  toastTimer = setTimeout(
    () => {
      node.hidden = true;
    },
    error ? 9000 : 4500,
  );
}

// Keep a stale page and any draft open if a direct download is refused. The
// generated anchor also uses download so a switch after this check cannot
// navigate the editor to a 409 response.
async function guardedDownload(event) {
  event.preventDefault();
  const link = event.currentTarget;
  if (
    !link.hasAttribute("href") ||
    link.getAttribute("aria-disabled") === "true"
  )
    return;
  const url = link.href;
  await api("/health", { background: true });
  if (!link.isConnected || link.href !== url) return;
  const download = document.createElement("a");
  download.href = url;
  download.download = link.getAttribute("download") || "";
  document.body.append(download);
  download.click();
  download.remove();
}

// Keep the preflight anchor outside Activity and dialogs, which can be redrawn
// while the instance check is in flight. The browser streams the response.
async function nativeExportDownload(url) {
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  link.hidden = true;
  document.body.append(link);
  try {
    await guardedDownload({ preventDefault() {}, currentTarget: link });
  } finally {
    link.remove();
  }
}

for (const id of ["original-link", "history-export", "annotation-export"])
  $(`#${id}`).addEventListener("click", (event) => {
    guardedDownload(event).catch((error) => toast(error.message, true));
  });

function handle(action) {
  return async (...args) => {
    let provider = args[0]?.target?.closest?.("#settings-form")
      ? providerContext()
      : null;
    if (provider) $("#provider-error").hidden = true;
    try {
      const result = action(...args);
      // Tab actions install their form context before awaiting the settings load.
      if (provider && args[0]?.target?.closest?.("[data-provider-tab]"))
        provider = providerContext();
      await result;
    } catch (error) {
      if (error.name === "AbortError") return;
      if (provider) {
        if (!sameProvider(provider)) return;
        const alert = $("#provider-error");
        alert.textContent = error.message;
        alert.hidden = false;
        alert.focus();
        alert.scrollIntoView({ block: "nearest" });
      } else
        toast(
          error.name === "NotAllowedError"
            ? "Click Play to allow browser playback."
            : error.message,
          true,
        );
    }
  };
}
function time(value) {
  if (value == null || !Number.isFinite(value)) return "—";
  const total = Math.floor(value),
    hours = Math.floor(total / 3600);
  return `${hours ? String(hours).padStart(2, "0") + ":" : ""}${String(Math.floor(total / 60) % 60).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}
function saved(label = "All changes saved") {
  if (label === "All changes saved" && state.recording?.snapshot_error)
    label = state.recording.snapshot_error;
  const status = $("#save-status");
  status.textContent = label;
  status.dataset.quiet = String(label === "All changes saved" || !label);
  $("#results-manage").disabled =
    label === "Saving…" || pendingResultMutations.has(state.recording?.id);
}
function media() {
  return state.media === "video" ? $("#video") : $("#audio");
}
function canLeave() {
  if (timedWordSaving || passageTextSaving) {
    toast("Wait for the current change to finish saving.", true);
    return false;
  }
  if (
    state.project &&
    savingProjects.has(state.project.id) &&
    $("#project-settings-dialog").open
  ) {
    toast("Wait for project settings to finish saving.", true);
    return false;
  }
  if (pendingBulkLanguage || bulkLanguageUnconfirmed) {
    toast(
      pendingBulkLanguage
        ? "Wait for the selected languages to finish saving."
        : "Reload the project to check the selected languages.",
      true,
    );
    return false;
  }
  if (state.dirty && !confirm("Discard this unsaved passage correction?"))
    return false;
  if (
    recordingNotesDirty() &&
    !confirm("Discard these unsaved recording notes?")
  )
    return false;
  return (
    !projectSettingsDirty() ||
    confirm("Discard these unsaved project settings?")
  );
}
// In-page passage actions only replace the open passage draft; notes stay on the page.
function canDiscardPassage() {
  if (timedWordSaving || passageTextSaving) {
    toast("Wait for the current change to finish saving.", true);
    return false;
  }
  return !state.dirty || confirm("Discard this unsaved passage correction?");
}
function clearEdit() {
  editVersion++;
  state.dirty = false;
  state.editing = null;
  passageRealignChoice = null;
  saved();
}
const pendingProjectLanguages = new Set();
const pendingRecordingLanguages = new Set();
let pendingBulkLanguage = false,
  bulkLanguageUnconfirmed = false;
function requireSettledLanguage(path, options) {
  const method = (options.method || "GET").toUpperCase();
  const recording = path.match(
    /^\/recordings\/([^/]+)\/(?:passage-timing|retranscriptions|enhancements|segments\/[^/]+(?:\/edit-v[123])?)$/u,
  );
  let ids = null;
  if (recording && (method === "POST" || method === "PATCH"))
    ids = [decodeURIComponent(recording[1])];
  else if (path === "/transcriptions" && method === "POST") {
    let body = options.body;
    if (typeof body === "string") {
      try {
        body = JSON.parse(body);
      } catch {
        body = null;
      }
    }
    ids = Array.isArray(body?.recording_ids) ? body.recording_ids : [];
    if (
      !ids.length &&
      (pendingRecordingLanguages.size || pendingProjectLanguages.size)
    )
      throw new Error(
        "Wait for language settings to finish saving before continuing.",
      );
  }
  if (!ids) return;
  if (bulkLanguageUnconfirmed)
    throw new Error(
      "Reload the project to check the selected languages before continuing.",
    );
  const waiting = ids.some((id) => {
    if (pendingRecordingLanguages.has(id)) return true;
    if (!pendingProjectLanguages.size) return false;
    const row =
      state.recording?.id === id
        ? state.recording
        : state.recordings.find((item) => item.id === id);
    if (!row) return true;
    const project = row.project_id || state.project?.id;
    return !project || pendingProjectLanguages.has(project);
  });
  if (waiting)
    throw new Error(
      "Wait for language settings to finish saving before continuing.",
    );
}
const languageDisplayNames =
  typeof Intl.DisplayNames === "function"
    ? new Intl.DisplayNames(["en"], { type: "language" })
    : null;
function languageName(code) {
  const found = languageCatalog.find((language) => language.code === code);
  if (found) return found.name;
  try {
    return languageDisplayNames?.of(code) || code;
  } catch {
    return code;
  }
}
function recordingProjectLanguage() {
  let project = state.project;
  if (state.recording) {
    const current =
      state.project?.id === state.recording.project_id ? state.project : null;
    const listed = state.projects.find(
      (item) => item.id === state.recording.project_id,
    );
    project =
      !listed ||
      (current &&
        (current.language_revision || 0) > (listed.language_revision || 0))
        ? current
        : listed;
  }
  return project?.default_language || null;
}
function recordingLanguageDefault(value) {
  const code = recordingProjectLanguage();
  if (!code) return "Use model/processing settings (no project default)";
  const name = languageName(code);
  return value ? `Use project default (${name})` : name;
}
function languageOptions(value, inherited, projectDefault = null) {
  const choices = [
    ["", inherited],
    [
      "da",
      !value && projectDefault === "da"
        ? "Danish (recording override)"
        : "Danish",
    ],
    [
      "en",
      !value && projectDefault === "en"
        ? "English (recording override)"
        : "English",
    ],
  ];
  if (value && !choices.some(([code]) => code === value))
    choices.push([value, languageName(value)]);
  choices.push(["__choose__", "Choose another language…"]);
  return choices
    .map(
      ([code, name]) =>
        `<option value="${h(code)}" ${code === (value || "") ? "selected" : ""}>${h(name)}</option>`,
    )
    .join("");
}
function renderLanguageSelect(select, value, inherited) {
  select.innerHTML = languageOptions(value, inherited);
}
function recordingLanguageOptions(value) {
  return languageOptions(
    value,
    recordingLanguageDefault(value),
    recordingProjectLanguage(),
  );
}
function renderRecordingLanguageSelect(select, value) {
  select.innerHTML = recordingLanguageOptions(value);
}
function refreshRecordingLanguageDefaults() {
  const code = recordingProjectLanguage();
  const selectors = state.recording
    ? [$("#file-language")]
    : document.querySelectorAll("[data-recording-language]");
  for (const select of selectors) {
    const option = select?.querySelector('option[value=""]');
    if (!select) continue;
    if (option) option.textContent = recordingLanguageDefault(select.value);
    for (const [value, name] of [
      ["da", "Danish"],
      ["en", "English"],
    ]) {
      const choice = select.querySelector(`option[value="${value}"]`);
      if (choice)
        choice.textContent =
          !select.value && value === code
            ? `${name} (recording override)`
            : name;
    }
    select.title = select.value
      ? "Recording language override"
      : code
        ? "Uses project default; changes with the project"
        : "Uses model or processing settings";
  }
}
let languageCatalogPromise = null,
  languagePickerResolver = null,
  languageCatalog = [],
  languagePickerGeneration = 0;
function renderLanguagePicker() {
  const query = $("#language-picker-search").value.trim().toLocaleLowerCase();
  const matches = languageCatalog.filter(({ code, name, aliases = [] }) =>
    [code, name, ...aliases].some((text) =>
      text.toLocaleLowerCase().includes(query),
    ),
  );
  $("#language-picker-list").innerHTML = matches
    .slice(0, 100)
    .map(
      ({ code, name }) =>
        `<option value="${h(code)}">${h(name)} (${h(code)})</option>`,
    )
    .join("");
  $("#language-picker-status").textContent =
    matches.length > 100
      ? `Showing 100 of ${matches.length} languages. Type to narrow the list.`
      : `${matches.length} language${matches.length === 1 ? "" : "s"} found.`;
}
async function chooseLanguage(select, previous, current = () => true) {
  if (select.value !== "__choose__") return select.value || null;
  select.value = previous || "";
  // A close event may still be queued when a different selector is opened.
  languagePickerResolver?.(undefined);
  languagePickerResolver = null;
  const generation = ++languagePickerGeneration;
  $("#language-picker-search").value = "";
  $("#language-picker-list").innerHTML = "";
  $("#language-picker-status").textContent = "Loading languages…";
  $("#language-picker-dialog").showModal();
  const selected = new Promise((resolve) => {
    languagePickerResolver = resolve;
  });
  $("#language-picker-search").focus();
  if (!languageCatalogPromise) {
    languageCatalogPromise = api("/languages").catch((error) => {
      languageCatalogPromise = null;
      throw error;
    });
  }
  try {
    const catalog = await languageCatalogPromise;
    if (
      generation !== languagePickerGeneration ||
      !$("#language-picker-dialog").open
    )
      return undefined;
    if (!current()) {
      $("#language-picker-dialog").close();
      return undefined;
    }
    languageCatalog = catalog.languages;
    renderLanguagePicker();
    return await selected;
  } catch (error) {
    if (
      generation === languagePickerGeneration &&
      $("#language-picker-dialog").open
    )
      $("#language-picker-dialog").close();
    throw error;
  }
}
$("#language-picker-search").addEventListener("input", renderLanguagePicker);
$("#language-picker-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = $("#language-picker-list").value;
  if (!value) return;
  const resolve = languagePickerResolver;
  languagePickerResolver = null;
  $("#language-picker-dialog").close();
  resolve?.(value);
});
$("#language-picker-dialog").addEventListener("close", () => {
  if ($("#language-picker-dialog").open) return;
  languagePickerGeneration++;
  languagePickerResolver?.(undefined);
  languagePickerResolver = null;
});
function updateLanguageControls() {
  if (state.project && !state.recording) {
    const select = $("#project-edit-language");
    renderLanguageSelect(
      select,
      $("#project-settings-dialog").open && projectForm?.id === state.project.id
        ? projectForm.languageDraft
        : state.project.default_language,
      "Use model settings",
    );
    select.disabled = pendingProjectLanguages.has(state.project.id);
    document.querySelectorAll("[data-recording-language]").forEach((item) => {
      const recording = state.recordings.find(
        (row) => row.id === item.dataset.recordingLanguage,
      );
      if (!recording) return;
      renderRecordingLanguageSelect(item, recording.language_override);
      item.disabled =
        pendingBulkLanguage ||
        bulkLanguageUnconfirmed ||
        pendingRecordingLanguages.has(recording.id);
    });
    updateSelection();
  }
  if (state.recording) {
    const select = $("#file-language");
    renderRecordingLanguageSelect(select, state.recording.language_override);
    select.disabled = pendingRecordingLanguages.has(state.recording.id);
    $("#transcribe-one").disabled =
      !canQueueOriginal(state.recording) ||
      !allowedProviders().length ||
      pendingRecordingLanguages.has(state.recording.id) ||
      pendingProjectLanguages.has(state.recording.project_id);
  }
  refreshRecordingLanguageDefaults();
}
async function saveLanguage(kind, id, value) {
  const project = kind === "projects";
  const pending = project ? pendingProjectLanguages : pendingRecordingLanguages;
  const item = project
    ? state.project?.id === id
      ? state.project
      : state.projects.find((row) => row.id === id)
    : state.recording?.id === id
      ? state.recording
      : state.recordings.find((row) => row.id === id);
  if (!item || item.id !== id || pending.has(id)) return;
  const revision = item.language_revision || 0;
  pending.add(id);
  updateLanguageControls();
  try {
    const result = await api(`/${kind}/${id}/language`, {
      method: "PUT",
      body: { language: value || null, revision },
    });
    const observed = project
      ? [state.project, ...state.projects]
      : [state.recording, ...state.recordings];
    for (const row of observed) {
      if (
        !row ||
        row.id !== id ||
        (row.language_revision || 0) > result.revision
      )
        continue;
      row[project ? "default_language" : "language_override"] = result.language;
      row.language_revision = result.revision;
    }
  } finally {
    pending.delete(id);
    updateLanguageControls();
  }
}
function nameDialog(title, initial = "", projectOptions = null) {
  $("#name-title").textContent = title;
  $("#name-input").value = initial;
  $("#new-project-fields").hidden = !projectOptions;
  $("#project-classification").required = !!projectOptions;
  if (projectOptions) {
    $("#project-classification").value = projectOptions.classification || "";
    renderLanguageSelect(
      $("#project-default-language"),
      null,
      "Use model settings",
    );
    $("#project-default-language").dataset.previousLanguage = "";
    $("#project-storage").value = projectOptions.storage_path || "";
    $("#project-storage").readOnly = false;
    $("#browse-project-storage").disabled = false;
    $("#project-storage-note").textContent =
      `A dedicated subfolder will be created here. Default: ${projectOptions.default_project_parent}${projectOptions.default_parent_synced ? " (a cloud-synced folder: choose a local folder for sensitive recordings)" : ""}. The central library (${projectOptions.library}) also stores transcripts and edits.`;
  }
  $("#name-dialog").showModal();
  $("#name-input").focus();
  return new Promise((resolve) => {
    nameResolver = resolve;
  });
}
$("#name-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = $("#name-input").value.trim();
  if (!value) return;
  const resolve = nameResolver;
  nameResolver = null;
  $("#name-dialog").close();
  resolve?.(
    $("#new-project-fields").hidden
      ? value
      : {
          name: value,
          classification: $("#project-classification").value,
          storage_parent: $("#project-storage").value || null,
          default_language: $("#project-default-language").value || null,
        },
  );
});
$("#name-dialog").addEventListener("close", () => {
  nameResolver?.(null);
  nameResolver = null;
});
$("#project-default-language").addEventListener(
  "change",
  handle(async (event) => {
    const select = event.target;
    const resolver = nameResolver;
    const value = await chooseLanguage(
      select,
      select.dataset.previousLanguage,
      () => $("#name-dialog").open && nameResolver === resolver,
    );
    if (value === undefined) return;
    renderLanguageSelect(select, value, "Use model settings");
    select.dataset.previousLanguage = value || "";
  }),
);
document
  .querySelectorAll("[data-close]")
  .forEach((button) =>
    button.addEventListener("click", () =>
      $(`#${button.dataset.close}`).close(),
    ),
  );

let projectReadGeneration = 0,
  recordingReadGeneration = 0,
  recordingListProject = null;
function preserveObservedNotes(rows, observed) {
  const knownNotes = new Map(),
    knownLanguages = new Map();
  for (const item of observed) {
    if (!item) continue;
    const previousNotes = knownNotes.get(item.id),
      previousLanguage = knownLanguages.get(item.id);
    if (
      !previousNotes ||
      (item.notes_revision || 0) > (previousNotes.notes_revision || 0)
    )
      knownNotes.set(item.id, item);
    if (
      !previousLanguage ||
      (item.language_revision || 0) > (previousLanguage.language_revision || 0)
    )
      knownLanguages.set(item.id, item);
  }
  for (const row of rows) {
    const previous = knownNotes.get(row.id),
      previousLanguage = knownLanguages.get(row.id);
    if (
      previous &&
      (previous.notes_revision || 0) > (row.notes_revision || 0)
    ) {
      row.notes = previous.notes;
      row.notes_revision = previous.notes_revision;
    }
    // Language is independently revisioned from notes and transcript edits.
    if (
      previousLanguage &&
      (previousLanguage.language_revision || 0) > (row.language_revision || 0)
    ) {
      row.language_revision = previousLanguage.language_revision;
      if ("language_override" in previousLanguage)
        row.language_override = previousLanguage.language_override;
      if ("default_language" in previousLanguage)
        row.default_language = previousLanguage.default_language;
    }
  }
  return rows;
}
async function refreshProjects() {
  const generation = ++projectReadGeneration;
  const projects = await api("/projects");
  if (generation !== projectReadGeneration) return false;
  state.projects = preserveObservedNotes(projects, [
    state.project,
    ...state.projects,
  ]);
  if (state.project)
    state.project =
      state.projects.find((p) => p.id === state.project.id) || null;
  renderProjects();
  refreshRecordingLanguageDefaults();
  return true;
}
function renderProjects() {
  $("#projects").innerHTML = state.projects
    .map(
      (project) =>
        `<a href="#/projects/${project.id}" class="project-link ${state.project?.id === project.id ? "active" : ""}"><span class="folder" aria-hidden="true">▱</span><span class="project-label">${h(project.name)}</span><span class="project-count">${project.recordings}</span></a>`,
    )
    .join("");
}
async function createProject() {
  const storage = await api("/storage");
  const choice = await nameDialog("Create a project", "", storage);
  if (!choice) return;
  const project = await api("/projects", { method: "POST", body: choice });
  await refreshProjects();
  location.hash = `/projects/${project.id}`;
}
["#sidebar-create", "#welcome-create"].forEach((id) =>
  $(id).addEventListener("click", handle(createProject)),
);
$("#browse-project-storage").addEventListener(
  "click",
  handle(async (event) => {
    event.target.disabled = true;
    try {
      const result = await api("/storage/browse", { method: "POST" });
      if (result.path) $("#project-storage").value = result.path;
    } finally {
      event.target.disabled = false;
    }
  }),
);
const PROJECT_SETTINGS_NOTE =
  "Save to apply changes. A new parent folder moves this project's files. Protected projects use approved providers only.";
let projectForm = null;
const savingProjects = new Set(),
  savingProjectNotes = new Set(),
  projectRefreshRequired = new Set();
function projectSaveMarker(id) {
  return `cbs-project-settings-unconfirmed:${id}:`;
}
function projectSaveMarkers(id) {
  const prefix = projectSaveMarker(id);
  return Array.from({ length: localStorage.length }, (_, i) =>
    localStorage.key(i),
  ).filter((key) => key?.startsWith(prefix));
}
function projectSaveUnconfirmed(id) {
  try {
    return projectSaveMarkers(id).length > 0;
  } catch {
    return true;
  }
}
function renderProjectSaveWarning(id) {
  const pending = projectSaveUnconfirmed(id);
  $("#project-save-unconfirmed").hidden = !pending;
  $("#project-settings-save").disabled =
    pending || savingProjects.has(id) || !!projectForm?.needsReload;
}
async function projectSettingsWrite(context, path, options) {
  const marker = projectSaveMarker(context.id) + crypto.randomUUID();
  try {
    localStorage.setItem(marker, "unconfirmed");
  } catch {
    throw new Error("Cannot record the save warning. No request was sent.");
  }
  const result = await api(path, options);
  localStorage.removeItem(marker);
  return result;
}
const projectNoteDrafts = new Map();
function notesValue(
  input,
  baseline = input.notesBaseline ?? input.defaultValue,
) {
  // Textareas normalize CR/CRLF, and HTML parsing removes an initial newline.
  // Keep exact saved wording when the displayed field has not actually changed.
  return input.value === baseline.replace(/\r\n?/g, "\n")
    ? baseline
    : input.value;
}
function projectSettingsDirty() {
  if (!projectForm || projectForm.id !== state.project?.id || state.recording)
    return false;
  return (
    (!savingProjects.has(projectForm.id) &&
      ($("#project-edit-name").value.trim() !== projectForm.name ||
        projectForm.languageDraft !== (projectForm.default_language || null) ||
        $("#project-edit-classification").value !==
          projectForm.classification ||
        $("#project-edit-storage").value.trim() !==
          projectForm.storage_path)) ||
    (!savingProjectNotes.has(projectForm.id) &&
      notesValue($("#project-notes"), projectForm.notes) !== projectForm.notes)
  );
}
function renderProjectSettings(preserveNotesDraft = false) {
  const notesDraft = preserveNotesDraft
    ? notesValue($("#project-notes"), projectForm?.notes || "")
    : null;
  const project = state.project;
  $("#project-settings-form").hidden = !project;
  $("#project-notes-panel").hidden = !project;
  $("#project-rename").hidden = !project;
  // Classification, storage and deletion live in Project Settings (sidebar, project pages).
  $("#project-settings-open").hidden = !project || !!state.recording;
  // Project actions sit in the sidebar above Project Settings (user layout, 23 Oct).
  $("#sidebar-project-actions").hidden = !project || !!state.recording;
  $("#sidebar-recording-actions").hidden = !state.recording;
  $("#project-description").hidden = !!project;
  projectForm = project
    ? {
        ...project,
        notes: project.notes || "",
        languageDraft: project.default_language || null,
        version: 0,
      }
    : null;
  if (!project) return;
  $("#project-rename").disabled = savingProjects.has(project.id);
  $("#project-edit-name").value = project.name;
  $("#project-edit-classification").value = project.classification;
  renderLanguageSelect(
    $("#project-edit-language"),
    project.default_language,
    "Use model settings",
  );
  $("#project-edit-language").disabled = pendingProjectLanguages.has(
    project.id,
  );
  $("#project-edit-storage").value = project.storage_path;
  $("#project-edit-storage").title = project.storage_path;
  $("#project-settings-fields").disabled = savingProjects.has(project.id);
  $("#project-settings-save").disabled = savingProjects.has(project.id);
  renderProjectSaveWarning(project.id);
  $("#project-settings-cancel").disabled = savingProjects.has(project.id);
  $("#project-settings-dialog [data-close]").disabled = savingProjects.has(
    project.id,
  );
  $("#project-delete").disabled = savingProjects.has(project.id);
  $("#browse-project-edit-storage").disabled = false;
  $("#project-edit-note").textContent = savingProjects.has(project.id)
    ? "Saving project settings…"
    : project.snapshot_error || PROJECT_SETTINGS_NOTE;
  renderProjectNotes();
  if (notesDraft !== null && !projectNoteDrafts.has(project.id)) {
    $("#project-notes").value = notesDraft;
    $("#project-notes-status").textContent =
      notesDraft !== projectForm.notes ? "Unsaved notes" : "All notes saved";
  }
}
function renderProjectNotes() {
  const project = state.project;
  if (!project) return;
  const draft = projectNoteDrafts.get(project.id);
  const retained =
    draft &&
    (savingProjectNotes.has(project.id) || draft.text !== draft.baseline)
      ? draft
      : null;
  if (retained && projectForm?.id === project.id) {
    projectForm.notes = retained.baseline;
    projectForm.notes_revision = retained.revision;
  }
  $("#project-notes").notesBaseline = retained?.baseline ?? project.notes ?? "";
  $("#project-notes").value = retained?.text ?? project.notes ?? "";
  $("#project-notes").disabled = savingProjectNotes.has(project.id);
  $("#project-notes-save").disabled = savingProjectNotes.has(project.id);
  $("#project-notes-status").textContent = savingProjectNotes.has(project.id)
    ? "Saving notes…"
    : retained?.status || "";
}
$("#project-notes").addEventListener("input", () => {
  if (!projectForm) return;
  $("#project-notes-status").textContent =
    notesValue($("#project-notes"), projectForm.notes) !== projectForm.notes
      ? "Unsaved notes"
      : "All notes saved";
  projectNoteDrafts.set(projectForm.id, {
    text: notesValue($("#project-notes"), projectForm.notes),
    baseline: projectForm.notes,
    revision: projectForm.notes_revision || 0,
    status: $("#project-notes-status").textContent,
  });
});
$("#project-notes-save").addEventListener(
  "click",
  handle(async () => {
    const context = projectForm;
    if (!context || savingProjectNotes.has(context.id)) return;
    const text = notesValue($("#project-notes"), context.notes);
    const frozen = {
      text,
      baseline: context.notes,
      revision: context.notes_revision || 0,
    };
    projectNoteDrafts.set(context.id, { ...frozen, status: "Saving notes…" });
    savingProjectNotes.add(context.id);
    $("#project-notes").disabled = true;
    $("#project-notes-save").disabled = true;
    $("#project-notes-status").textContent = "Saving notes…";
    let succeeded = false;
    try {
      let result = await api(`/projects/${context.id}/notes`, {
        method: "PATCH",
        body: { text, revision: frozen.revision },
      });
      for (const observed of [state.project, ...state.projects])
        if (
          observed?.id === context.id &&
          (observed.notes_revision || 0) > result.notes_revision
        )
          result = observed;
      const fields = {
        notes: result.notes,
        notes_revision: result.notes_revision,
      };
      Object.assign(context, fields);
      for (const project of state.projects)
        if (project.id === context.id) Object.assign(project, fields);
      if (state.project?.id === context.id)
        Object.assign(state.project, fields);
      projectNoteDrafts.set(context.id, {
        text: fields.notes,
        baseline: fields.notes,
        revision: fields.notes_revision,
        status: "All notes saved",
      });
      succeeded = true;
      if (projectForm === context)
        $("#project-notes-status").textContent = "All notes saved";
    } catch (error) {
      projectNoteDrafts.set(context.id, {
        ...frozen,
        status: `Save failed — notes retained. ${error.message}`,
      });
      if (projectForm === context)
        $("#project-notes-status").textContent = error.message;
      throw error;
    } finally {
      savingProjectNotes.delete(context.id);
      if (state.project?.id === context.id && !state.recording) {
        if (projectForm?.id === context.id) {
          projectForm.notes = state.project.notes || "";
          projectForm.notes_revision = state.project.notes_revision || 0;
        }
        renderProjectNotes();
        if (succeeded && projectForm === context)
          $("#project-notes-status").textContent = "All notes saved";
        $("#project-notes").disabled = false;
        $("#project-notes-save").disabled = false;
      }
    }
  }),
);
$("#project-rename").addEventListener(
  "click",
  handle(async () => {
    const context = projectForm;
    if (!context || savingProjects.has(context.id)) return;
    if (
      projectSaveUnconfirmed(context.id) ||
      projectRefreshRequired.has(context.id)
    )
      throw new Error(
        "A previous project settings request may still finish. Open Project settings to review the warning.",
      );
    const name = await nameDialog("Rename project", context.name);
    if (
      !name ||
      name === context.name ||
      projectForm !== context ||
      projectSaveUnconfirmed(context.id) ||
      projectRefreshRequired.has(context.id) ||
      state.recording
    )
      return;
    savingProjects.add(context.id);
    $("#project-rename").disabled = true;
    $("#project-settings-fields").disabled = true;
    try {
      await projectSettingsWrite(context, `/projects/${context.id}`, {
        method: "PATCH",
        body: { name, expected_name: context.name },
      });
      if (!(await refreshProjects()))
        throw new Error("Project list is still refreshing. Try again.");
      if (state.project?.id === context.id && !state.recording) {
        projectForm.name = state.project.name;
        $("#project-title").textContent = state.project.name;
        document.title = `${state.project.name} · CBS Transcriber`;
      }
    } catch (error) {
      projectRefreshRequired.add(context.id);
      throw error;
    } finally {
      savingProjects.delete(context.id);
      if (state.project?.id === context.id && !state.recording) {
        $("#project-rename").disabled = false;
        $("#project-settings-fields").disabled = false;
        $("#project-edit-note").textContent = projectSettingsDirty()
          ? "Unsaved project settings"
          : "All project settings saved";
      }
    }
  }),
);
$("#project-settings-form").addEventListener("input", () => {
  if (!projectForm) return;
  projectForm.version++;
  $("#project-edit-note").textContent = projectSettingsDirty()
    ? "Unsaved project settings"
    : "All project settings saved";
});
$("#browse-project-edit-storage").addEventListener(
  "click",
  handle(async (event) => {
    const context = projectForm,
      version = context.version;
    event.target.disabled = true;
    $("#project-edit-note").textContent =
      "Choose a folder in the window that opened. It can take a few seconds to appear.";
    try {
      const result = await api("/storage/browse", { method: "POST" });
      if (
        result.path &&
        projectForm === context &&
        $("#project-settings-dialog").open &&
        context.version === version
      ) {
        $("#project-edit-storage").value = result.path;
        $("#project-edit-storage").title = result.path;
        $("#project-edit-storage").dispatchEvent(
          new Event("input", { bubbles: true }),
        );
      }
    } finally {
      if (projectForm === context) {
        event.target.disabled = false;
        if (!savingProjects.has(context.id))
          $("#project-edit-note").textContent = projectSettingsDirty()
            ? "Unsaved project settings"
            : PROJECT_SETTINGS_NOTE;
      }
    }
  }),
);
$("#project-edit-language").addEventListener(
  "change",
  handle(async (event) => {
    const id = state.project?.id,
      epoch = viewEpoch;
    const current = () =>
      viewEpoch === epoch &&
      state.project?.id === id &&
      $("#project-settings-dialog").open;
    const value = await chooseLanguage(
      event.target,
      projectForm?.languageDraft,
      current,
    );
    if (value !== undefined && current()) {
      projectForm.languageDraft = value;
      renderLanguageSelect(event.target, value, "Use model settings");
      projectForm.version++;
      $("#project-edit-note").textContent = projectSettingsDirty()
        ? "Unsaved project settings"
        : PROJECT_SETTINGS_NOTE;
    }
  }),
);
function resetProjectSettingsFields(context) {
  $("#project-edit-name").value = context.name;
  $("#project-edit-classification").value = context.classification;
  context.languageDraft = context.default_language || null;
  renderLanguageSelect(
    $("#project-edit-language"),
    context.languageDraft,
    "Use model settings",
  );
  $("#project-edit-storage").value = context.storage_path;
  $("#project-edit-storage").title = context.storage_path;
}
$("#project-settings-form").addEventListener(
  "submit",
  handle(async (event) => {
    event.preventDefault();
    const context = projectForm;
    if (!context || context.needsReload || savingProjects.has(context.id))
      return;
    if (projectSaveUnconfirmed(context.id)) {
      renderProjectSaveWarning(context.id);
      return;
    }
    const choice = {
      name: $("#project-edit-name").value.trim(),
      classification: $("#project-edit-classification").value,
      storage_parent: $("#project-edit-storage").value.trim(),
      language: context.languageDraft,
    };
    if (
      choice.name === context.name &&
      choice.language === (context.default_language || null) &&
      choice.classification === context.classification &&
      choice.storage_parent === context.storage_path
    ) {
      $("#project-settings-dialog").close();
      return;
    }
    if (!choice.name) throw new Error("Enter a project name.");
    if (!choice.storage_parent) {
      throw new Error("Enter an absolute storage folder or choose one.");
    }
    if (
      choice.storage_parent !== context.storage_path &&
      !confirm(
        `Move this project's files into a new folder under:
${choice.storage_parent}

Large recordings can take time to copy.`,
      )
    ) {
      $("#project-edit-note").textContent =
        "Move cancelled. No changes were saved.";
      return;
    }
    savingProjects.add(context.id);
    $("#project-rename").disabled = true;
    $("#project-settings-fields").disabled = true;
    $("#project-settings-save").disabled = true;
    $("#project-settings-cancel").disabled = true;
    $("#project-settings-dialog [data-close]").disabled = true;
    $("#project-delete").disabled = true;
    let failure = null,
      warning = null,
      savedFields = [];
    try {
      if (choice.name !== context.name) {
        await projectSettingsWrite(context, `/projects/${context.id}`, {
          method: "PATCH",
          body: { name: choice.name, expected_name: context.name },
        });
        context.name = choice.name;
        savedFields.push("name");
      }
      if (choice.classification !== context.classification) {
        await projectSettingsWrite(
          context,
          `/projects/${context.id}/classification`,
          {
            method: "PATCH",
            body: {
              classification: choice.classification,
              expected_classification: context.classification,
            },
          },
        );
        context.classification = choice.classification;
        savedFields.push("classification");
      }
      if (choice.language !== (context.default_language || null)) {
        pendingProjectLanguages.add(context.id);
        updateLanguageControls();
        try {
          const result = await projectSettingsWrite(
            context,
            `/projects/${context.id}/language`,
            {
              method: "PUT",
              body: {
                language: choice.language,
                revision: context.language_revision || 0,
              },
            },
          );
          context.default_language = result.language;
          context.language_revision = result.revision;
          savedFields.push("language");
        } finally {
          pendingProjectLanguages.delete(context.id);
          updateLanguageControls();
        }
      }
      if (choice.storage_parent !== context.storage_path) {
        $("#project-edit-note").textContent = "Moving project files…";
        const moved = await projectSettingsWrite(
          context,
          `/projects/${context.id}/storage`,
          {
            method: "POST",
            body: {
              storage_parent: choice.storage_parent,
              expected_storage_path: context.storage_path,
            },
          },
        );
        context.storage_path = moved.path;
        savedFields.push("storage folder");
        warning = moved.cleanup_warning;
        if (projectForm === context) {
          $("#project-edit-storage").value = moved.path;
          $("#project-edit-storage").title = moved.path;
        }
      }
    } catch (error) {
      failure = error;
      context.needsReload = true;
    } finally {
      try {
        if (!(await refreshProjects()))
          throw new Error("Project list is still refreshing. Try again.");
        projectRefreshRequired.delete(context.id);
      } catch (error) {
        failure ||= error;
        projectRefreshRequired.add(context.id);
      } finally {
        savingProjects.delete(context.id);
        if (state.project?.id === context.id && !state.recording) {
          if (projectForm !== context) renderProjectSettings(true);
          $("#project-settings-fields").disabled = false;
          $("#project-settings-save").disabled = !!failure;
          renderProjectSaveWarning(context.id);
          if (failure) $("#project-settings-save").disabled = true;
          $("#project-settings-cancel").disabled = false;
          $("#project-settings-dialog [data-close]").disabled = false;
          $("#project-delete").disabled = false;
          $("#project-rename").disabled = false;
          $("#project-title").textContent = state.project.name;
          document.title = `${state.project.name} · CBS Transcriber`;
          $("#project-edit-note").textContent =
            (failure &&
              `${savedFields.length ? `Confirmed saved: ${savedFields.join(", ")}. ` : ""}A previous request may still finish. ${failure.message}`) ||
            warning ||
            state.project.snapshot_error ||
            "All project settings saved";
          renderProviderSelects();
        }
      }
    }
    if (failure) throw failure;
    if (projectForm === context && $("#project-settings-dialog").open)
      $("#project-settings-dialog").close();
    if (warning) toast(warning, true);
  }),
);
$("#project-settings-dialog").addEventListener("cancel", (event) => {
  if (state.project && savingProjects.has(state.project.id))
    event.preventDefault();
});
$("#project-save-acknowledge").addEventListener(
  "click",
  handle(() => {
    const context = projectForm;
    if (!context || savingProjects.has(context.id)) return;
    const markers = projectSaveMarkers(context.id);
    if (
      !confirm(
        "The previous request may still finish later. Continue editing despite this uncertainty?",
      )
    )
      return;
    for (const marker of markers) localStorage.removeItem(marker);
    renderProjectSaveWarning(context.id);
  }),
);
window.addEventListener("storage", (event) => {
  if (
    projectForm &&
    event.key?.startsWith(projectSaveMarker(projectForm.id)) &&
    $("#project-settings-dialog").open
  )
    renderProjectSaveWarning(projectForm.id);
});
$("#project-settings-dialog").addEventListener("close", () => {
  if ($("#project-settings-dialog").open) return;
  if (projectForm) projectForm.version++;
  if (
    state.project &&
    !state.recording &&
    !savingProjects.has(state.project.id)
  )
    renderProjectSettings(true);
});

async function route() {
  const discardCardNotes = !state.recording && recordingNotesDirty();
  const discardEditorNotes = state.recording && recordingNotesDirty();
  const discardProjectNotes =
    !state.recording &&
    projectForm &&
    !savingProjectNotes.has(projectForm.id) &&
    notesValue($("#project-notes"), projectForm.notes) !== projectForm.notes;
  if (!canLeave()) {
    history.replaceState(
      null,
      "",
      state.recording
        ? `#/recordings/${state.recording.id}`
        : `#/projects/${state.project.id}`,
    );
    return;
  }
  if ($("#language-picker-dialog").open) $("#language-picker-dialog").close();
  $("#bulk-language-status").textContent = "";
  $("#export-dialog").close();
  exportContext = null;
  $("#history-dialog").close();
  $("#results-dialog").close();
  $("#result-merge-dialog").close();
  $("#working-version-dialog").close();
  $("#wording-adoption-dialog").close();
  $("#project-settings-dialog").close();
  $("#collection-settings-dialog").close();
  collectionDialogContext = null;
  $("#delete-project-dialog").close();
  $("#research-report-dialog").close();
  $("#annotations-dialog").close();
  $("#passage-merge-dialog").close();
  passageMergeContext = null;
  if (discardEditorNotes) {
    cardNoteDrafts.delete(state.recording.id);
    $("#recording-notes").value = $("#recording-notes").defaultValue;
  }
  if (discardCardNotes) {
    state.recordings.forEach((recording) =>
      cardNoteDrafts.delete(recording.id),
    );
    document.querySelectorAll("[data-card-notes] textarea").forEach((input) => {
      input.value = input.defaultValue;
    });
  }
  if (discardProjectNotes) projectNoteDrafts.delete(projectForm.id);
  clearEdit();
  const match = location.hash.match(
    /^#\/(projects|recordings)\/([a-f0-9]{32})$/,
  );
  const epoch = ++viewEpoch;
  cancelMediaHandoff();
  if (match?.[1] === "recordings") {
    const recording = await api(`/recordings/${match[2]}`);
    if (epoch !== viewEpoch) return;
    // A recording preference may finish saving while this detail read is in flight.
    preserveObservedNotes(
      [recording],
      [state.recordings.find((item) => item.id === recording.id)],
    );
    state.project = state.projects.find(
      (project) => project.id === recording.project_id,
    );
    state.recording = recording;
    renderProjects();
    await openEditor();
    return;
  }
  $("#audio").pause();
  $("#video").pause();
  $("#player").hidden = true;
  state.recording = null;
  state.comparison = null;
  state.project = match
    ? state.projects.find((p) => p.id === match[2])
    : state.projects[0] || null;
  state.selected.clear();
  updateSelection();
  $("#library-view").hidden = false;
  $("#editor-view").hidden = true;
  $("#sidebar-recording-actions").hidden = true;
  $("#file-settings-open").hidden = true;
  $("#file-settings-dialog").close();
  $("#welcome").hidden = !!state.project;
  $("#project-content").hidden = !state.project;
  $("#project-title").textContent =
    state.project?.name || "Make room for the conversation.";
  renderProjectSettings();
  document.title = state.project
    ? `${state.project.name} · CBS Transcriber`
    : "CBS Transcriber";
  renderProjects();
  if (state.project) await refreshRecordings();
  renderProviderSelects();
}
window.addEventListener("hashchange", handle(route));
async function refreshRecordings() {
  if (!state.project) return;
  const projectId = state.project.id,
    epoch = viewEpoch,
    generation = ++recordingReadGeneration;
  const [recordings, collections] = await Promise.all([
    api(`/projects/${projectId}/recordings`),
    api(`/projects/${projectId}/collections`),
  ]);
  if (
    generation !== recordingReadGeneration ||
    epoch !== viewEpoch ||
    state.project?.id !== projectId ||
    state.recording
  )
    return;
  state.recordings = preserveObservedNotes(recordings, state.recordings);
  recordingListProject = projectId;
  if (state.collectionProject !== projectId) state.collection = null;
  state.collectionProject = projectId;
  state.collections = collections;
  if (
    state.collection &&
    state.collection !== "unsorted" &&
    !collections.some((c) => c.id === state.collection)
  )
    state.collection = null;
  const ids = new Set(state.recordings.map((r) => r.id));
  for (const id of state.selected) if (!ids.has(id)) state.selected.delete(id);
  renderRecordings();
}
function renderRecordings(captureNotes = true) {
  if (captureNotes)
    document.querySelectorAll("[data-card-notes]").forEach(rememberCardNotes);
  renderCollections();
  const recordings = visibleRecordings();
  $("#recording-count").textContent = collectionRecordings().length;
  $("#recordings").innerHTML = recordings
    .map((recording) => {
      const active = state.jobs.find(
        (j) =>
          j.recording_id === recording.id &&
          ["queued", "running"].includes(j.state),
      );
      const status = active
        ? active.kind === "media"
          ? active.state === "queued"
            ? "Queued for preparation"
            : "Preparing"
          : active.kind === "enhance"
            ? active.state === "queued"
              ? "Queued for enhancement"
              : "Enhancing"
            : "Transcribing"
        : recording.status === "error"
          ? "Needs attention"
          : recording.has_transcript
            ? "Transcript ready"
            : recording.status === "ready"
              ? "Ready to transcribe"
              : "Preparing";
      return `<article class="recording-card ${state.selected.has(recording.id) ? "selected" : ""}" data-recording="${recording.id}"><div class="card-top"><span class="file-icon" aria-hidden="true">${recording.kind === "video" ? "▷" : "♫"}</span><label><span class="sr-only">Select ${h(recording.name)}</span><input type="checkbox" data-select="${recording.id}" ${state.selected.has(recording.id) ? "checked" : ""}></label><button class="icon-button danger" data-delete="${recording.id}" aria-label="Delete ${h(recording.name)}" title="Delete recording" ${pendingDeletions.has(`/recordings/${recording.id}`) ? "disabled" : ""}>×</button></div><button class="card-name" data-open="${recording.id}">${h(recording.name)}</button><div class="card-file">${h(recording.filename)}</div><div class="card-bottom"><span class="badge ${active ? "working" : recording.status === "error" ? "failed" : !recording.has_transcript ? "neutral" : ""}">${status}</span><span>${time(recording.duration)} · ${h(recording.filename.split(".").pop().toUpperCase())}</span></div><label class="card-language"><select aria-label="Language for ${h(recording.name)}" data-recording-language="${h(recording.id)}" ${pendingRecordingLanguages.has(recording.id) ? "disabled" : ""}>${recordingLanguageOptions(recording.language_override)}</select></label>${cardNotesMarkup(recording)}${pendingDeletions.has(`/recordings/${recording.id}`) ? `<p class="settings-note" role="status">${h(pendingDeletions.get(`/recordings/${recording.id}`))}</p>` : ""}<small class="card-results">${recording.transcript_count || 0} results</small></article>`;
    })
    .join("");
  refreshRecordingLanguageDefaults();
  restoreCardNoteDrafts();
  $("#empty-project").hidden = !!state.recordings.length;
  updateSelection();
}
function updateSelection() {
  const selected = state.recordings.filter((recording) =>
    state.selected.has(recording.id),
  );
  const currentList =
    !!state.project && recordingListProject === state.project.id;
  $("#recordings").hidden = !currentList;
  if (!currentList) $("#empty-project").hidden = true;
  $("#transcribe-selected").disabled =
    !currentList ||
    pendingBulkLanguage ||
    bulkLanguageUnconfirmed ||
    !state.selected.size ||
    !allowedProviders().length ||
    pendingProjectLanguages.has(state.project?.id) ||
    selected.some((recording) => pendingRecordingLanguages.has(recording.id)) ||
    selected.length !== state.selected.size ||
    !selected.every(canQueueOriginal);
  const visible = visibleRecordings();
  const hiddenSelected =
    state.selected.size -
    visible.filter((r) => state.selected.has(r.id)).length;
  const selectionDescription = `${state.selected.size} selected${hiddenSelected ? `; ${hiddenSelected} hidden by the current filter` : ""}`;
  $("#transcribe-selected").title = selectionDescription;
  $("#deselect-all").title = selectionDescription;
  $("#select-all").disabled =
    !currentList ||
    pendingBulkLanguage ||
    bulkLanguageUnconfirmed ||
    !visible.length ||
    visible.every((recording) => state.selected.has(recording.id));
  $("#deselect-all").disabled =
    pendingBulkLanguage || bulkLanguageUnconfirmed || !state.selected.size;
  $("#bulk-language").disabled =
    !currentList ||
    pendingBulkLanguage ||
    bulkLanguageUnconfirmed ||
    !state.selected.size ||
    state.selected.size > 1000 ||
    selected.length !== state.selected.size ||
    selected.some((recording) => pendingRecordingLanguages.has(recording.id));
  $("#bulk-language").title =
    state.selected.size > 1000
      ? "Choose at most 1,000 recordings for one language change."
      : "Apply a language to all selected recordings.";
  document.querySelectorAll("[data-select]").forEach((input) => {
    input.disabled =
      !currentList || pendingBulkLanguage || bulkLanguageUnconfirmed;
  });
  document.querySelectorAll("[data-recording-language]").forEach((input) => {
    input.disabled =
      !currentList ||
      pendingBulkLanguage ||
      bulkLanguageUnconfirmed ||
      pendingRecordingLanguages.has(input.dataset.recordingLanguage);
  });
  $("#bulk-collection").disabled =
    !currentList ||
    pendingBulkLanguage ||
    bulkLanguageUnconfirmed ||
    !state.selected.size ||
    !(state.collections || []).length;
  document.querySelectorAll("#collection-chips button").forEach((button) => {
    button.disabled =
      !currentList || pendingBulkLanguage || bulkLanguageUnconfirmed;
  });
}
function canQueueOriginal(recording) {
  // Admission verifies the managed original in the background. Playback status
  // describes a separate browser derivative and cannot prove original validity.
  return (
    !!recording?.id &&
    /\.(mp3|wav|m4a|flac|ogg|mp4|mov|mkv|webm|mpeg|mpg|mpga|aac|wma|avi)$/i.test(
      recording.filename || "",
    )
  );
}
// Collections (ADR 0060): chips filter the cards; selection and exports follow the chip.
function visibleRecordings() {
  const filter = $("#recording-search").value.toLowerCase();
  return state.recordings.filter(
    (r) =>
      (!state.collection
        ? true
        : state.collection === "unsorted"
          ? !r.collection_id
          : r.collection_id === state.collection) &&
      `${r.name} ${r.filename}`.toLowerCase().includes(filter),
  );
}
// Recordings in the shown collection, regardless of the search box (for counts).
function collectionRecordings() {
  return state.recordings.filter((r) =>
    !state.collection
      ? true
      : state.collection === "unsorted"
        ? !r.collection_id
        : r.collection_id === state.collection,
  );
}
function renderCollections() {
  const collections = state.collections || [];
  const chip = (value, label, count) =>
    `<button type="button" class="collection-chip" data-collection="${h(value)}" aria-pressed="${String((state.collection || "") === value)}">${h(label)} <span class="count">${count}</span></button>`;
  const unsorted = state.recordings.filter((r) => !r.collection_id).length;
  $("#collection-chips").innerHTML =
    chip("", "All", state.recordings.length) +
    collections
      .map((c) =>
        chip(
          c.id,
          c.name,
          state.recordings.filter((r) => r.collection_id === c.id).length,
        ),
      )
      .join("") +
    (collections.length ? chip("unsorted", "Unsorted", unsorted) : "") +
    '<button type="button" class="collection-chip new" id="collection-new">＋ New collection</button>';
  $("#bulk-collection").innerHTML =
    '<option value="">Move selected to…</option>' +
    collections
      .map((c) => `<option value="${h(c.id)}">${h(c.name)}</option>`)
      .join("") +
    '<option value="unsorted">Unsorted</option>';
  renderSidebarSettings();
}
function shownCollection() {
  return (state.collections || []).find((c) => c.id === state.collection);
}
// Inside a collection, Collection Settings take Project Settings' place (user layout).
function renderSidebarSettings() {
  const collection = !state.recording && shownCollection();
  $("#collection-settings-open").hidden = !state.project || !collection;
  $("#project-settings-open").hidden =
    !state.project || !!state.recording || !!collection;
}
let collectionDialogContext = null,
  collectionSelectionVersion = 0;
const pendingCollectionActions = new Set();
function currentCollectionDialog(context) {
  return (
    !!context &&
    context === collectionDialogContext &&
    $("#collection-settings-dialog").open &&
    state.project?.id === context.projectId &&
    !state.recording
  );
}
function renderCollectionControls() {
  const context = collectionDialogContext;
  $("#collection-settings-form button[type=submit]").disabled = $(
    "#collection-delete",
  ).disabled = !context || pendingCollectionActions.has(context.id);
}
$("#collection-chips").addEventListener(
  "click",
  handle(async (event) => {
    if (pendingBulkLanguage || bulkLanguageUnconfirmed) return;
    if (event.target.closest("#collection-new")) {
      const projectId = state.project.id,
        epoch = viewEpoch;
      const name = prompt("Name the new collection");
      if (!name?.trim()) return;
      const selection = ++collectionSelectionVersion;
      const created = await api(`/projects/${projectId}/collections`, {
        method: "POST",
        body: { name: name.trim() },
      });
      if (
        epoch !== viewEpoch ||
        state.project?.id !== projectId ||
        state.recording
      )
        return;
      if (selection === collectionSelectionVersion) {
        state.collection = created.id;
        collectionSelectionVersion++;
        state.selected.clear();
      }
      await refreshRecordings();
      return;
    }
    const chip = event.target.closest("[data-collection]");
    if (!chip) return;
    collectionSelectionVersion++;
    state.collection = chip.dataset.collection || null;
    state.selected.clear();
    renderRecordings();
  }),
);
$("#bulk-collection").addEventListener(
  "change",
  handle(async (event) => {
    const value = event.target.value;
    event.target.value = "";
    if (!value || !state.selected.size) return;
    const projectId = state.project.id,
      epoch = viewEpoch,
      selection = collectionSelectionVersion,
      selected = state.selected,
      ids = [...state.selected];
    const moved = await api(`/projects/${projectId}/collections/move`, {
      method: "POST",
      body: {
        recording_ids: ids,
        collection_id: value === "unsorted" ? null : value,
      },
    });
    if (
      epoch === viewEpoch &&
      state.project?.id === projectId &&
      !state.recording
    ) {
      if (
        selection === collectionSelectionVersion &&
        selected === state.selected
      )
        state.selected.clear();
      await refreshRecordings();
    }
    toast(`Moved ${moved.moved} recording(s).`);
  }),
);
$("#collection-settings-open").addEventListener("click", () => {
  const collection = shownCollection();
  if (!collection) return;
  collectionSelectionVersion++;
  collectionDialogContext = {
    id: collection.id,
    projectId: state.project.id,
    version: 0,
  };
  $("#collection-settings-title").textContent =
    `Collection settings · ${collection.name}`;
  $("#collection-name").value = collection.name;
  $("#collection-notes").value = collection.notes || "";
  // The values shown are the baseline: only fields changed from them are saved.
  $("#collection-settings-form").dataset.name = collection.name;
  $("#collection-settings-form").dataset.notes = collection.notes || "";
  $("#collection-status").textContent = "";
  renderCollectionControls();
  $("#collection-settings-dialog").showModal();
});
$("#collection-settings-form").addEventListener("input", () => {
  if (!collectionDialogContext) return;
  collectionDialogContext.version++;
  $("#collection-status").textContent = "Unsaved changes";
});
$("#collection-settings-form").addEventListener(
  "submit",
  handle(async (event) => {
    event.preventDefault();
    const context = collectionDialogContext;
    if (
      !currentCollectionDialog(context) ||
      pendingCollectionActions.has(context.id)
    )
      return;
    const version = context.version;
    const form = $("#collection-settings-form");
    const changes = {};
    if ($("#collection-name").value.trim() !== form.dataset.name)
      changes.name = $("#collection-name").value.trim();
    const notes = notesValue($("#collection-notes"), form.dataset.notes);
    if (notes !== form.dataset.notes) changes.notes = notes;
    if (!Object.keys(changes).length) {
      $("#collection-status").textContent = "No changes to save.";
      return;
    }
    pendingCollectionActions.add(context.id);
    $("#collection-settings-form button[type=submit]").disabled = $(
      "#collection-delete",
    ).disabled = true;
    try {
      await api(`/collections/${context.id}`, {
        method: "PATCH",
        body: changes,
      });
      if (currentCollectionDialog(context)) {
        form.dataset.name = changes.name ?? form.dataset.name;
        form.dataset.notes = changes.notes ?? form.dataset.notes;
        $("#collection-status").textContent =
          context.version === version ? "Saved." : "Unsaved changes";
      }
      if (state.project?.id === context.projectId && !state.recording)
        await refreshRecordings();
    } finally {
      pendingCollectionActions.delete(context.id);
      renderCollectionControls();
    }
  }),
);
$("#collection-delete").addEventListener(
  "click",
  handle(async () => {
    const context = collectionDialogContext;
    const collection = state.collections?.find(
      (item) =>
        item.id === context?.id && item.project_id === context?.projectId,
    );
    if (
      !currentCollectionDialog(context) ||
      pendingCollectionActions.has(context.id) ||
      !collection ||
      !confirm(
        `Delete the collection "${collection.name}"? Its recordings stay in the project as Unsorted.`,
      )
    )
      return;
    pendingCollectionActions.add(context.id);
    $("#collection-settings-form button[type=submit]").disabled = $(
      "#collection-delete",
    ).disabled = true;
    try {
      await api(`/collections/${context.id}`, { method: "DELETE" });
      if (currentCollectionDialog(context)) {
        $("#collection-settings-dialog").close();
        collectionDialogContext = null;
      }
      if (state.project?.id === context.projectId && !state.recording) {
        if (state.collection === context.id) state.collection = null;
        await refreshRecordings();
      }
    } finally {
      pendingCollectionActions.delete(context.id);
      renderCollectionControls();
    }
  }),
);
$("#recording-search").addEventListener("input", renderRecordings);
$("#select-all").addEventListener("click", () => {
  if (pendingBulkLanguage || bulkLanguageUnconfirmed) return;
  $("#bulk-language-status").textContent = "";
  collectionSelectionVersion++;
  for (const recording of visibleRecordings()) state.selected.add(recording.id);
  renderRecordings();
});
$("#deselect-all").addEventListener("click", () => {
  if (pendingBulkLanguage || bulkLanguageUnconfirmed) return;
  $("#bulk-language-status").textContent = "";
  collectionSelectionVersion++;
  state.selected.clear();
  renderRecordings();
});
$("#bulk-language").addEventListener(
  "change",
  handle(async (event) => {
    const select = event.target,
      choice = select.value;
    if (!choice || pendingBulkLanguage || bulkLanguageUnconfirmed) return;
    const projectId = state.project?.id,
      epoch = viewEpoch,
      selection = collectionSelectionVersion,
      rows = state.recordings.filter((row) => state.selected.has(row.id)),
      captured = rows.map((row) => ({
        id: row.id,
        revision: row.language_revision || 0,
      }));
    const current = () =>
      viewEpoch === epoch &&
      state.project?.id === projectId &&
      !state.recording &&
      collectionSelectionVersion === selection;
    if (
      !captured.length ||
      captured.length !== state.selected.size ||
      captured.length > 1000
    )
      return;
    const value =
      choice === "__inherit__"
        ? null
        : await chooseLanguage(select, "", current);
    select.value = "";
    if (value === undefined || !current()) return;
    // An earlier collection move must not clear the selection while this saves.
    collectionSelectionVersion++;
    pendingBulkLanguage = true;
    for (const row of captured) pendingRecordingLanguages.add(row.id);
    $("#bulk-language-status").textContent =
      `Saving language for ${captured.length} recordings…`;
    updateLanguageControls();
    try {
      const result = await api(`/projects/${projectId}/recording-languages`, {
        method: "PUT",
        body: { language: value, recordings: captured },
      });
      for (const item of result.recordings) {
        for (const row of [state.recording, ...state.recordings]) {
          if (
            row?.id === item.id &&
            (row.language_revision || 0) <= item.revision
          ) {
            row.language_override = item.language;
            row.language_revision = item.revision;
          }
        }
      }
      $("#bulk-language-status").textContent =
        result.snapshot_error ||
        `Language saved for ${captured.length} recordings.`;
    } catch (error) {
      bulkLanguageUnconfirmed = true;
      if (error.status === 409) {
        try {
          await refreshRecordings();
          bulkLanguageUnconfirmed = false;
          $("#bulk-language-status").textContent =
            "A selected recording changed. Review the refreshed values and try again.";
        } catch {
          $("#bulk-language-status").textContent =
            "Could not refresh the selected languages. Reload the project before continuing.";
        }
      } else {
        $("#bulk-language-status").textContent =
          "Could not confirm the language save. Reload the project and check the values before continuing.";
      }
    } finally {
      for (const row of captured) pendingRecordingLanguages.delete(row.id);
      pendingBulkLanguage = false;
      updateLanguageControls();
    }
  }),
);
$("#recordings").addEventListener(
  "change",
  handle(async (event) => {
    const languageId = event.target.dataset.recordingLanguage;
    if (languageId) {
      const epoch = viewEpoch,
        id = state.project?.id;
      const current = () =>
        viewEpoch === epoch && state.project?.id === id && !state.recording;
      const recording = state.recordings.find((row) => row.id === languageId);
      const value = await chooseLanguage(
        event.target,
        recording?.language_override,
        current,
      );
      if (value !== undefined && current())
        await saveLanguage("recordings", languageId, value);
      return;
    }
    const id = event.target.dataset.select;
    if (!id) return;
    if (pendingBulkLanguage || bulkLanguageUnconfirmed) return;
    $("#bulk-language-status").textContent = "";
    collectionSelectionVersion++;
    if (event.target.checked) state.selected.add(id);
    else state.selected.delete(id);
    event.target
      .closest(".recording-card")
      .classList.toggle("selected", event.target.checked);
    updateSelection();
  }),
);
$("#recordings").addEventListener(
  "click",
  handle(async (event) => {
    const open = event.target.closest("[data-open]");
    if (open) {
      location.hash = `/recordings/${open.dataset.open}`;
      return;
    }
    const del = event.target.closest("[data-delete]");
    if (
      del &&
      !del.disabled &&
      canLeave() &&
      confirm(
        "Cancel this recording's jobs and delete its app-managed files? Your original source file is unaffected.",
      )
    ) {
      const epoch = viewEpoch,
        projectId = state.project.id;
      const current = () =>
        epoch === viewEpoch &&
        state.project?.id === projectId &&
        !state.recording;
      try {
        const deleted = await deleteManaged(
          `/recordings/${del.dataset.delete}`,
          current,
          () => renderRecordings(),
        );
        if (!deleted || !current()) return;
      } finally {
        if (current()) renderRecordings();
      }
      await refreshProjects();
      if (current()) await refreshRecordings();
    }
  }),
);
$("#project-settings-open").addEventListener(
  "click",
  handle(async () => {
    if (!state.project) return;
    const id = state.project.id,
      epoch = viewEpoch;
    if (projectRefreshRequired.has(id)) {
      if (!(await refreshProjects()))
        throw new Error("Project list is still refreshing. Try again.");
      projectRefreshRequired.delete(id);
      if (viewEpoch !== epoch || state.project?.id !== id || state.recording)
        return;
    }
    // Fresh values from the saved project; an open notes draft on the page is kept.
    if (!savingProjects.has(state.project.id)) renderProjectSettings(true);
    $("#project-settings-title").textContent =
      `Project settings · ${state.project.name}`;
    $("#project-settings-dialog").showModal();
  }),
);
// Deletion is deliberately out of the way and needs the exact project name.
let projectDeleteContext = null;
$("#project-delete").addEventListener("click", () => {
  if (!state.project) return;
  projectDeleteContext = { id: state.project.id, name: state.project.name };
  $("#project-settings-dialog").close();
  $("#delete-project-name").textContent = state.project.name;
  $("#delete-project-confirm").value = "";
  $("#delete-project-error").hidden = true;
  $("#delete-project-progress").hidden = true;
  $("#delete-project-progress").textContent = "";
  $("#delete-project-submit").disabled = true;
  $("#delete-project-dialog").dataset.project = state.project.id;
  $("#delete-project-dialog").showModal();
});
$("#delete-project-confirm").addEventListener("input", (event) => {
  $("#delete-project-submit").disabled =
    event.target.value.trim() !== projectDeleteContext?.name;
});
$("#delete-project-form").addEventListener(
  "submit",
  handle(async (event) => {
    event.preventDefault();
    const project = projectDeleteContext;
    const dialog = $("#delete-project-dialog");
    if (
      !project ||
      dialog.dataset.project !== project.id ||
      $("#delete-project-confirm").value.trim() !== project.name
    )
      return;
    const button = $("#delete-project-submit");
    const epoch = viewEpoch;
    const sameDialog = () =>
      projectDeleteContext === project &&
      dialog.dataset.project === project.id &&
      dialog.open;
    const current = () =>
      epoch === viewEpoch &&
      location.hash === `#/projects/${project.id}` &&
      sameDialog();
    button.disabled = true;
    $("#delete-project-error").hidden = true;
    try {
      const deleted = await deleteManaged(
        `/projects/${project.id}`,
        current,
        (message) => {
          $("#delete-project-progress").textContent = message;
          $("#delete-project-progress").hidden = false;
        },
      );
      if (!deleted || !current()) return;
    } catch (error) {
      if (!current()) return;
      // Keep the reason inside the dialog (e.g. unresolved UCloud jobs block deletion).
      $("#delete-project-error").textContent = error.message;
      $("#delete-project-error").hidden = false;
      return;
    } finally {
      if (sameDialog()) {
        $("#delete-project-progress").hidden = true;
        $("#delete-project-progress").textContent = "";
        button.disabled =
          $("#delete-project-confirm").value.trim() !== project.name;
      }
    }
    dialog.close();
    await refreshProjects();
    if (epoch !== viewEpoch || location.hash !== `#/projects/${project.id}`)
      return;
    location.hash = "";
    await route();
  }),
);
$("#add-files").addEventListener("click", () => $("#media-files").click());
$("#empty-project").addEventListener("click", () => $("#media-files").click());
$("#empty-project").addEventListener("keydown", (e) => {
  if (["Enter", " "].includes(e.key)) {
    e.preventDefault();
    $("#media-files").click();
  }
});
$("#media-files").addEventListener(
  "change",
  handle(async (event) => {
    await uploadFiles(event.target.files);
    event.target.value = "";
  }),
);
const dropzone = $("#empty-project");
dropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropzone.classList.add("dragging");
});
dropzone.addEventListener("dragleave", () =>
  dropzone.classList.remove("dragging"),
);
dropzone.addEventListener(
  "drop",
  handle(async (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragging");
    await uploadFiles(event.dataTransfer.files);
  }),
);
async function uploadFiles(files) {
  if (!state.project) return;
  const projectId = state.project.id;
  const collection =
    state.collection && state.collection !== "unsorted"
      ? state.collection
      : null;
  // V162: files added together share one batch ID, so Activity shows one
  // Media preparation card for them; a single file keeps its own card.
  const batch =
    files.length > 1
      ? [...crypto.getRandomValues(new Uint8Array(16))]
          .map((byte) => byte.toString(16).padStart(2, "0"))
          .join("")
      : null;
  for (const file of files) {
    $("#upload-status").textContent = `Adding ${file.name}…`;
    const data = new FormData();
    data.append("project_id", projectId);
    // Uploads made while a collection is shown go into it (ADR 0060).
    if (collection) data.append("collection_id", collection);
    if (batch) data.append("upload_batch", batch);
    data.append("file", file);
    try {
      await api("/recordings", { method: "POST", body: data });
    } catch (error) {
      toast(`${file.name}: ${error.message}`, true);
    }
  }
  // The new cards and Activity show the result; no lingering status text.
  $("#upload-status").textContent = "";
  await refreshProjects();
  if (state.project?.id === projectId) await refreshRecordings();
  await pollJobs();
}
async function enqueue(
  ids,
  providerIds = [state.defaultProvider],
  confirmed = false,
  requestedFeatures = null,
  enhancementOptions = null,
) {
  if (!ids.length || !canLeave()) return;
  const names = ids.map(
    (id) =>
      state.recordings.find((r) => r.id === id)?.name ||
      state.recording?.name ||
      "recording",
  );
  await loadProviders();
  const selectedProviders = providerIds.map((id) =>
    state.providers.find((p) => p.id === id),
  );
  if (selectedProviders.some((p) => !p))
    throw new Error("A selected provider no longer exists.");
  const missingKey = selectedProviders.find(
    (p) =>
      (p.base_url === OPENAI_URL ||
        ["ucloud-vllm", "ucloud"].includes(p.provider_type)) &&
      (!p.has_key || !p.base_url),
  );
  if (missingKey) {
    await openSettings(missingKey.id);
    toast("Add your API key before transcribing.");
    return;
  }
  await requireRuntimeBatchBackend(providerIds);
  if (
    !confirmed &&
    !confirm(
      `Transcribe ${names.length} recording(s) using:\n${selectedProviders.map((p) => `${configurationLabel(p.name, p.model)} · ${p.model} at ${p.base_url}`).join("\n")}\nA separate result is saved for each provider. Audio is sent to these providers and their charges apply.${enhancementOptions ? `\nMissing features will then run through ${{ ucloud: "your UCloud connection (batch job, no SSH)", auto: "this computer or UCloud, chosen per recording by length", local: "local processing" }[enhancementOptions.execution]}. Both results are kept.` : ""}`,
    )
  )
    return;
  const request = {
    recording_ids: ids,
    provider_ids: providerIds,
    requested_features: requestedFeatures,
    enhancement: enhancementOptions,
  };
  try {
    await api("/transcriptions", { method: "POST", body: request });
  } catch (error) {
    // An unprepared UCloud runtime is asked about once for the whole selection, before
    // anything is accepted; standing consent in Settings → UCloud skips this (ADR 0098).
    if (error.code !== "prepare_runtime" || !confirm(error.message))
      throw error;
    await api("/transcriptions", {
      method: "POST",
      body: { ...request, prepare_runtime: true },
    });
  }
  clearEdit();
  await pollJobs();
  $("#activity").hidden = false;
  toast("Added to the transcription queue");
  return true;
}
// UCloud models run by the CBS WhisperX runtime add speaker labels within the same job.
function whisperxProvider(providerId) {
  const provider = state.providers.find((p) => p.id === providerId);
  return (
    provider?.provider_type === "ucloud" &&
    ucloudModels().find((m) => m.model === provider.model)?.engine ===
      "whisperx"
  );
}
async function requireRuntimeBatchBackend(providerIds) {
  if (!providerIds.some(whisperxProvider)) return;
  try {
    await api("/runtime-batches/settings");
  } catch (error) {
    if (error.status === 404)
      throw new Error(
        "The app backend needs to be restarted before CBS Runtime batches can run. No job was queued.",
      );
    throw error;
  }
}
async function startTranscription(ids, providerId, scope) {
  if (
    pendingBulkLanguage ||
    bulkLanguageUnconfirmed ||
    pendingProjectLanguages.has(state.project?.id) ||
    ids.some((id) => pendingRecordingLanguages.has(id))
  ) {
    throw new Error(
      "Wait for language settings to finish saving before transcribing.",
    );
  }
  const requestedFeatures = transcriptionFeatures(scope, providerId);
  const native = nativeFeatures(providerId);
  if (
    !whisperxProvider(providerId) &&
    Object.keys(native).some((key) => requestedFeatures[key] && !native[key])
  ) {
    if (!ids.length || !canLeave()) return;
    await openEnhancementDialog({
      transcription: { ids, providerIds: [providerId], requestedFeatures },
    });
    return;
  }
  return enqueue(ids, [providerId], false, requestedFeatures);
}
$("#transcribe-selected").addEventListener(
  "click",
  handle(() =>
    startTranscription(
      [...state.selected],
      $("#bulk-provider").value,
      "selected",
    ),
  ),
);
$("#transcribe-one").addEventListener(
  "click",
  handle(() =>
    startTranscription(
      [state.recording.id],
      $("#file-provider").value,
      state.recording.id,
    ),
  ),
);
$("#file-language").addEventListener(
  "change",
  handle(async (event) => {
    const id = state.recording?.id,
      epoch = viewEpoch;
    const current = () => viewEpoch === epoch && state.recording?.id === id;
    const value = await chooseLanguage(
      event.target,
      state.recording?.language_override,
      current,
    );
    if (value !== undefined && current())
      await saveLanguage("recordings", id, value);
  }),
);
async function openEditor() {
  cancelMediaHandoff();
  state.comparison = null;
  $("#project-settings-open").hidden = true;
  $("#collection-settings-open").hidden = true;
  $("#sidebar-project-actions").hidden = true;
  $("#sidebar-recording-actions").hidden = false;
  // Inside a recording the sidebar offers File Settings instead of Project Settings.
  $("#file-settings-open").hidden = false;
  $("#project-settings-dialog").close();
  $("#library-view").hidden = true;
  $("#editor-view").hidden = false;
  document.title = `${state.recording.name} · CBS Transcriber`;
  renderEditorDetails();
  renderTranscript();
  state.media = "audio";
  state.replayEnd = null;
  $("#audio").pause();
  $("#video").pause();
  $("#audio").src = CBSLibraries.resource(
    `/api/recordings/${state.recording.id}/media/audio`,
  );
  $("#video").removeAttribute("src");
  if (state.recording.kind === "video" && state.recording.status === "ready")
    $("#video").src = CBSLibraries.resource(
      `/api/recordings/${state.recording.id}/media/video`,
    );
  $("#player").hidden = false;
  $("#video-stage").hidden = true;
  $("#view-mode").value = "audio";
  $("#playback-speed").value = "1";
  $("#view-mode-label").hidden = state.recording.kind !== "video";
  $("#play-pause").disabled = state.recording.status !== "ready";
  $("#play-transcript").disabled = state.recording.status !== "ready";
  $("#seek").value = 0;
  $("#current-time").textContent = "00:00";
}
function renderEditorDetails() {
  renderRecordingNotes();
  renderRecordingRenameControl();
  const r = state.recording;
  $("#recording-title").textContent = r.name;
  $("#recording-meta").textContent =
    `${time(r.duration)} · ${r.kind === "video" ? "Video recording" : "Audio recording"} · ${r.segments.length} passages`;
  $("#detail-file").textContent = r.filename;
  $("#detail-duration").textContent = time(r.duration);
  $("#detail-kind").textContent =
    r.status === "ready" ? "Ready for review" : "Preparing / check Activity";
  $("#player-name").textContent = r.name;
  $("#total-time").textContent = time(r.duration);
  $("#transcribe-one").disabled =
    !canQueueOriginal(r) ||
    !allowedProviders().length ||
    pendingProjectLanguages.has(r.project_id) ||
    pendingRecordingLanguages.has(r.id);
  renderRecordingLanguageSelect($("#file-language"), r.language_override);
  $("#file-language").disabled = pendingRecordingLanguages.has(r.id);
  refreshRecordingLanguageDefaults();
  $("#transcribe-one").title =
    r.status === "ready"
      ? ""
      : "Transcription uses the original file; browser playback preparation runs separately.";
  renderProviderPair(
    "#file-provider-kind",
    "#file-provider",
    r.provider_id || state.defaultProvider,
  );
  renderTranscriptionFeatures();
  renderResults();
  const ids = [...new Set(r.segments.map((s) => s.speaker))];
  $("#speakers").innerHTML = ids.length
    ? ids
        .map(
          (id, index) =>
            `<button class="speaker-entry" data-speaker="${h(id)}"><span class="speaker-dot s${index % 4}" aria-hidden="true"></span><span>${h(speakerName(r, id))}</span><small>↗</small></button>`,
        )
        .join("")
    : '<p class="field-note">No speaker labels yet</p>';
  $("#timing-badge").textContent = r.segments.some((s) => playbackWords(s).size)
    ? "Word timing"
    : r.segments.some((s) => s.start !== null)
      ? "Passage timing"
      : "Untimed";
  $("#original-link").href = CBSLibraries.resource(
    `/api/recordings/${r.id}/media/original`,
  );
  updateExport();
}
let exportContext = null;
function openExport(context) {
  if (!canLeave()) return;
  context.epoch = viewEpoch;
  context.collection = context.project ? state.collection : null;
  context.recording = context.project ? null : state.recording.id;
  context.recordings = context.project
    ? collectionRecordings().map(({ id, name, export_eligible }) => ({
        id,
        name,
        export_eligible: { ...export_eligible },
      }))
    : [
        {
          id: state.recording.id,
          name: state.recording.name,
          export_eligible: { active: !!context.result?.segments?.length },
        },
      ];
  context.recordingsById = new Map(
    context.recordings.map((recording) => [recording.id, recording]),
  );
  context.pending = null;
  exportContext = context;
  $("#export-originals").checked = false;
  if (context.project) $("#export-results").value = "working";
  $("#export-results-label").hidden = !context.project;
  $("#export-batch-note").hidden = !context.project;
  $("#export-scope").textContent = context.project
    ? `${state.project.name}${context.collection ? ` · ${shownCollection()?.name || "Unsorted"}` : ""}. Choose recordings and formats. Unassigned Working versions appear as skipped in the report.`
    : context.result
      ? `${state.recording.name} · ${resultLabel(context.result)}. Exports saved changes.`
      : `${state.recording.name} · No saved transcript.`;
  $("#export-recording-list").innerHTML = context.recordings
    .map(
      (recording) =>
        `<label class="export-choice"><input type="checkbox" data-export-recording value="${h(recording.id)}" ${exportTranscriptEligible(context, recording) ? "checked" : ""}><span>${h(recording.name)}</span><small data-export-reason class="field-note" hidden></small></label>`,
    )
    .join("");
  $("#export-status").textContent = "";
  updateExport();
  $("#export-dialog").showModal();
  CBSBackgroundExports.refresh();
}
$("#export-open").addEventListener("click", () => {
  const result = state.recording.transcripts.find(
    (t) => t.id === state.recording.active_transcript_id,
  );
  openExport({ result: result ? { ...state.recording, ...result } : null });
});
$("#project-export-open").addEventListener("click", () =>
  openExport({ project: state.project.id }),
);
function currentExport(context) {
  return (
    exportContext === context &&
    $("#export-dialog").open &&
    context.epoch === viewEpoch &&
    (context.project
      ? context.project === state.project?.id && !state.recording
      : context.recording === state.recording?.id)
  );
}
function selectedExportFormats() {
  return [...$("#export-formats").querySelectorAll("input:checked")].map(
    (input) => input.value,
  );
}
function selectedExportRecordings() {
  return [...$("#export-recording-list").querySelectorAll("input:checked")].map(
    (input) => input.value,
  );
}
function exportTranscriptEligible(context, recording) {
  return (
    recording?.export_eligible?.[
      context.project ? $("#export-results").value : "active"
    ] === true
  );
}
function updateExportRecordings(context, originals) {
  let eligibleCount = 0;
  for (const input of $("#export-recording-list").querySelectorAll("input")) {
    const recording = context.recordingsById.get(input.value);
    const transcript = exportTranscriptEligible(context, recording);
    const eligible = transcript || originals;
    if (eligible) eligibleCount++;
    else input.checked = false;
    input.disabled = !!context.pending || !eligible;
    const reason = input.closest("label").querySelector("[data-export-reason]");
    reason.hidden = transcript;
    reason.textContent =
      context.project &&
      $("#export-results").value === "working" &&
      recording?.export_eligible?.all
        ? "No Working transcript"
        : "No transcript";
  }
  return eligibleCount;
}
function updateExport() {
  if (!exportContext) return;
  const context = exportContext;
  const originals = $("#export-originals").checked;
  const eligibleCount = updateExportRecordings(context, originals);
  const formats = selectedExportFormats();
  const selected = selectedExportRecordings();
  const format = formats[0] || "";
  const readable = formats.includes("text");
  $("#export-group-label").hidden = !readable;
  $("#export-instructions").hidden = !formats.some((value) =>
    value.startsWith("nvivo-"),
  );
  const windows = formats.includes("nvivo-windows");
  $("#export-help").textContent = [
    windows
      ? "Windows: open the matching media in NVivo, turn on Edit, then Import Rows. Choose Tab Delimited Line and tick File includes header row. Map Timespan → Timespan, Speaker → Speaker and Content → Content. Select UTF-8 if needed."
      : "",
    formats.includes("nvivo-mac")
      ? "Mac: open the matching media in NVivo, turn on Edit, then Import → Files → Transcript Rows. Choose the file and UTF-8 encoding. Review imported speakers: NVivo can treat colon-space in transcript text as another speaker label."
      : "",
  ]
    .filter(Boolean)
    .join(" ");
  $("#export-docs").href =
    `https://help-nv.qsrinternational.com/20/${windows ? "win" : "mac"}/Content/files/import-audio-video-transcripts.htm`;
  const subtitles = formats.some((value) => ["srt", "vtt"].includes(value));
  const segments = context.result?.segments || [];
  const unavailable = context.project
    ? []
    : formats.filter(
        (value) =>
          (value.startsWith("nvivo-") &&
            segments.some((s) => s.start == null)) ||
          (["srt", "vtt"].includes(value) &&
            segments.some(
              (s) =>
                s.start == null ||
                s.end == null ||
                Math.round(s.end * 1000) <= Math.round(s.start * 1000),
            )),
      );
  const untimed = unavailable.length > 0;
  const allUntimed = untimed && unavailable.length === formats.length;
  const empty = !context.project && !segments.length;
  $("#export-warning").hidden =
    !untimed &&
    (!empty || originals) &&
    selected.length > 0 &&
    formats.length > 0 &&
    !formats.some((value) =>
      ["srt", "vtt", "csv", "json", "docx"].includes(value),
    );
  $("#export-warning").textContent = !selected.length
    ? "Choose at least one recording."
    : !formats.length && !originals
      ? "Choose at least one format."
      : !formats.length
        ? "Original recordings only. No transcript formats are included."
        : empty && !originals
          ? "Import or transcribe a recording before exporting."
          : untimed
            ? allUntimed && !originals
              ? "Some passages lack required timestamps. Choose Readable text, CSV, JSON or Word to export every passage."
              : "Some selected formats lack required timestamps and will be listed as skipped in the ZIP report. Other selected formats are included."
            : subtitles
              ? "One cue per timed passage; long passages may need subtitle editing. No missing timing is invented."
              : formats.includes("docx")
                ? "One paragraph per passage with start time and speaker, for comments and tracked changes in Word. Untimed passages are included; edits in Word do not return to this app."
                : formats.includes("csv")
                  ? "Current text and speaker names. Formula-like text cells get a leading apostrophe for spreadsheet safety."
                  : "Current text, speaker names and usable word timestamps. Original versions and provider settings are excluded.";
  const zipped = !!context.project || originals || formats.length > 1;
  const disabled =
    context.pending ||
    (zipped && CBSBackgroundExports.blocked()) ||
    (!originals && (allUntimed || empty || !formats.length)) ||
    !selected.length;
  $("#export-dialog")
    .querySelectorAll("input:not([data-export-recording]), select")
    .forEach((input) => (input.disabled = !!context.pending));
  $("#export-select-all").disabled =
    !!context.pending || selected.length === eligibleCount;
  $("#export-deselect-all").disabled = !!context.pending || !selected.length;
  if (disabled) {
    $("#export-link").removeAttribute("href");
    $("#export-link").setAttribute("aria-disabled", "true");
  } else {
    $("#export-link").removeAttribute("aria-disabled");
    $("#export-link").href = CBSLibraries.resource(
      context.project
        ? `/api/projects/${context.project}/export-tasks`
        : originals || formats.length > 1
          ? `/api/recordings/${context.recording}/export-tasks`
          : `/api/recordings/${context.recording}/export?format=${format}&grouping=${$("#export-group").value}${context.result?.id ? `&result_id=${encodeURIComponent(context.result.id)}` : ""}`,
    );
  }
  $("#export-link").textContent =
    context.project || originals || formats.length > 1
      ? "Prepare ZIP"
      : "↓ Export transcript";
  CBSBackgroundExports.dialogStatus(context, zipped);
  $("#export-batch-note").textContent = originals
    ? "The ZIP includes original audio/video for the selected recordings beside their transcripts. Unavailable files or transcript formats are listed in its report."
    : "The ZIP includes selected transcript formats and a report of exported or skipped recordings and formats. Original recordings are not included.";
}
$("#export-recording-list").addEventListener("change", updateExport);
$("#export-formats").addEventListener("change", updateExport);
$("#export-originals").addEventListener("change", updateExport);
for (const [id, checked] of [
  ["export-select-all", true],
  ["export-deselect-all", false],
])
  $(`#${id}`).addEventListener("click", () => {
    if (!exportContext || exportContext.pending) return;
    $("#export-recording-list")
      .querySelectorAll("input")
      .forEach((input) => {
        if (!input.disabled) input.checked = checked;
      });
    updateExport();
  });
$("#export-dialog").addEventListener("close", () => {
  if (!$("#export-dialog").open) exportContext = null;
});
$("#export-link").addEventListener("click", async (event) => {
  event.preventDefault();
  const link = event.currentTarget;
  if (
    !link.hasAttribute("href") ||
    link.getAttribute("aria-disabled") === "true"
  )
    return;
  const context = exportContext;
  if (!context || !currentExport(context) || context.pending) return;
  const url = link.href;
  const formats = selectedExportFormats();
  const recordingIds = selectedExportRecordings();
  const originals = $("#export-originals").checked;
  const zipped = !!context.project || originals || formats.length > 1;
  const body = zipped
    ? {
        formats,
        grouping: $("#export-group").value,
        ...(originals ? { include_originals: true } : {}),
        ...(context.project
          ? {
              recording_ids: recordingIds,
              results: $("#export-results").value,
              ...(context.collection ? { collection: context.collection } : {}),
            }
          : { result_id: context.result?.id ?? null }),
      }
    : null;
  const operation = {};
  context.pending = operation;
  context.backgroundExportError = false;
  updateExport();
  $("#export-status").textContent = zipped
    ? "Starting ZIP preparation…"
    : "Starting download…";
  try {
    if (zipped) {
      await CBSBackgroundExports.start(
        context.project
          ? `/projects/${context.project}/export-tasks`
          : `/recordings/${context.recording}/export-tasks`,
        body,
        () => currentExport(context) && context.pending === operation,
      );
      if (currentExport(context) && context.pending === operation)
        $("#export-status").textContent =
          "Export progress and downloads are in Activity. You can close this window.";
    } else {
      // Preserve the existing small, single-transcript download contract. ZIPs
      // are prepared separately and never copied into a browser Blob.
      const response = await fetch(url, {
        headers: {
          "X-CBS-Request": "1",
          ...CBSLibraries.instanceHeaders(),
        },
      });
      if (!currentExport(context) || context.pending !== operation) return;
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        if (!currentExport(context) || context.pending !== operation) return;
        CBSLibraries.stale(error);
        throw new Error(error.detail || "Export failed.");
      }
      const blob = await response.blob();
      if (!currentExport(context) || context.pending !== operation) return;
      const blobUrl = URL.createObjectURL(blob),
        download = document.createElement("a"),
        disposition = response.headers.get("Content-Disposition") || "",
        encodedName = disposition.match(/filename\*=utf-8''([^;]+)/i)?.[1];
      download.href = blobUrl;
      download.download = encodedName
        ? decodeURIComponent(encodedName)
        : disposition.match(/filename="([^"]+)"/i)?.[1] || "transcript.txt";
      download.click();
      $("#export-status").textContent = "Downloaded.";
      setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    }
  } catch (error) {
    if (currentExport(context) && context.pending === operation) {
      context.backgroundExportError = true;
      $("#export-status").textContent = error.message;
    }
  } finally {
    if (currentExport(context) && context.pending === operation) {
      context.pending = null;
      updateExport();
    }
  }
});
function resultLabel(result) {
  const label =
    result.source.kind === "provider"
      ? configurationLabel(result.label, result.source.model)
      : result.label;
  return `${label} · ${new Date(result.created).toLocaleString("en-GB")}`;
}
function renderResults() {
  const r = state.recording;
  const working = r.result_roles?.working;
  const workingResult = r.transcripts.find(
    (result) => result.id === working?.result_id,
  );
  $("#working-version-status").textContent = working
    ? `Working transcript: ${workingResult?.label || "Saved result"} · Version ${working.sequence}. Project exports use this saved version.`
    : "No Working version designated. Project exports will skip this recording.";
  $("#working-mark-current").disabled =
    !r.active_transcript_id ||
    pendingResultMutations.has(r.id) ||
    (working?.result_id === r.active_transcript_id &&
      !!r.active_version?.id &&
      working.version_id === r.active_version.id);
  $("#results-manage").disabled =
    pendingResultMutations.has(r.id) ||
    $("#save-status").textContent === "Saving…";
  $("#topics-open").disabled = !r.active_transcript_id;
  $("#export-open").disabled = pendingResultMutations.has(r.id);
  const results = (r.transcripts || []).filter((result) => !result.trashed_at);
  $("#result-select").innerHTML = results.length
    ? results
        .map(
          (t) =>
            `<option value="${t.id}" ${t.id === r.active_transcript_id ? "selected" : ""}>${h(resultLabel(t))}</option>`,
        )
        .join("")
    : '<option value="">No transcript yet</option>';
  $("#result-select").disabled =
    !results.length || pendingResultMutations.has(r.id);
  if (
    state.comparison &&
    (state.comparison.id === r.active_transcript_id ||
      !results.some((result) => result.id === state.comparison.id))
  )
    state.comparison = null;
  $("#compare-result").innerHTML =
    '<option value="">No comparison</option>' +
    results
      .filter((t) => t.id !== r.active_transcript_id)
      .map(
        (t) =>
          `<option value="${t.id}" ${t.id === state.comparison?.id ? "selected" : ""}>${h(resultLabel(t))}</option>`,
      )
      .join("");
  $("#compare-result").disabled =
    results.length < 2 || pendingResultMutations.has(r.id);
  const selected = results.find((t) => t.id === r.active_transcript_id);
  const ready = results.findLast(
    (t) =>
      t.source.kind === "enhancement" &&
      t.source.source_result_id === r.active_transcript_id,
  );
  $("#enhancement-ready").hidden = !ready;
  $("#enhancement-ready").innerHTML = ready
    ? `Enhancement saved separately${ready.source_changed ? ` from version ${ready.source_version_sequence} of this result; edits made here since then are not in it` : ""}. <button class="text-button" data-review-enhancement="${h(ready.id)}">Review enhanced result ↗</button>`
    : "";
  // A derived result never absorbs later edits to its source; say so instead of rebasing.
  const origin =
    selected?.source_changed &&
    results.find((t) => t.id === selected.source.source_result_id);
  $("#result-source-note").hidden = !selected?.source_changed;
  $("#result-source-note").textContent = selected?.source_changed
    ? `Built from version ${selected.source_version_sequence} of ${origin ? resultLabel(origin) : "its source result"}, which has been edited since (now version ${selected.source_head_sequence}). Those later edits are not in this result.`
    : "";
  renderComparison();
}
$("#enhancement-ready").addEventListener(
  "click",
  handle(async (event) => {
    const button = event.target.closest("[data-review-enhancement]");
    if (button && canLeave())
      await reviewResult(button.dataset.reviewEnhancement);
  }),
);
$("#result-select").addEventListener(
  "change",
  handle(async (event) => {
    if (!canLeave()) {
      event.target.value = state.recording.active_transcript_id;
      return;
    }
    await reviewResult(event.target.value);
  }),
);
async function reviewResult(id) {
  const context = recordingContext();
  if (pendingResultMutations.has(context.id)) return;
  pendingResultMutations.add(context.id);
  try {
    renderResults();
    const recording = await api(
      `/recordings/${context.id}/transcripts/${id}/activate`,
      { method: "POST", body: { revision: state.recording.revision } },
    );
    if (!acceptRecording(context, recording)) return;
    clearEdit();
    state.replayEnd = null;
    renderEditorDetails();
    renderTranscript();
  } catch (error) {
    if (context.epoch === viewEpoch && context.id === state.recording?.id)
      throw error;
  } finally {
    pendingResultMutations.delete(context.id);
    if (context.epoch === viewEpoch && context.id === state.recording?.id)
      renderResults();
  }
}
let resultsContext = null;
const pendingResultMutations = new Set();
function currentResults(context) {
  return (
    resultsContext === context &&
    $("#results-dialog").open &&
    sameRecordingView(context)
  );
}
function resultManagerError(error) {
  const alert = $("#results-error");
  alert.textContent = error.message;
  alert.hidden = false;
  alert.focus();
}
function renderManagedResults(context) {
  const busy = pendingResultMutations.has(context.id) || context.loading;
  const resultRow = (result, trashed) => {
    const label =
      result.source.kind === "provider"
        ? configurationLabel(result.label, result.source.model)
        : result.label;
    const marker =
      !trashed &&
      (result.id === context.recording.active_transcript_id
        ? "Currently reviewing"
        : result.id === state.comparison?.id
          ? "Comparison"
          : "");
    const merge =
      !trashed &&
      context.result &&
      result.source.kind === "enhancement" &&
      result.source.source_result_id === context.result &&
      result.id !== context.result;
    const conflict =
      !trashed && typeof conflictReviewButton === "function"
        ? conflictReviewButton(result, context, busy)
        : "";
    return `<article class="managed-result" data-managed-result="${h(result.id)}"><div><strong>${h(label)}</strong><span class="field-note">${h(new Date(result.created).toLocaleString("en-GB"))}${trashed ? ` · Moved to Trash ${h(new Date(result.trashed_at).toLocaleString("en-GB"))}` : ""}</span>${marker ? `<span class="badge neutral">${marker}</span>` : ""}${context.recording.result_roles?.working?.result_id === result.id ? `<span class="badge neutral">Contains Working version ${context.recording.result_roles.working.sequence}</span>` : ""}</div>${merge ? `<button class="button" type="button" data-preview-result-merge="${h(result.id)}" ${busy ? "disabled" : ""}>Preview merge</button>` : ""}${conflict}<button class="button" type="button" ${trashed ? "data-restore-result" : "data-trash-result"}="${h(result.id)}" aria-label="${h(trashed ? "Restore " + label : "Move " + label + " to Trash")}" ${busy ? "disabled" : ""}>${trashed ? "Restore" : "Move to Trash"}</button></article>`;
  };
  $("#result-list").innerHTML =
    (context.recording?.transcripts || [])
      .map((result) => resultRow(result, false))
      .join("") || '<p class="field-note">No saved results.</p>';
  $("#result-trash-list").innerHTML =
    (context.trash || []).map((result) => resultRow(result, true)).join("") ||
    '<p class="field-note">Trash is empty.</p>';
  $("#results-refresh").disabled = !!busy;
}
async function loadManagedResults(context, message = "", recording = null) {
  context.loading = true;
  $("#results-error").hidden = true;
  $("#results-status").textContent = message || "Loading results…";
  renderManagedResults(context);
  try {
    const [current, trash] = await Promise.all([
      recording || api(`/recordings/${context.id}`),
      api(`/recordings/${context.id}/trashed-transcripts`),
    ]);
    if (!currentResults(context)) return;
    context.recording = current;
    context.trash = trash;
    $("#results-status").textContent =
      message ||
      `${current.transcripts.length} saved results · ${trash.length} in Trash`;
  } catch (error) {
    if (currentResults(context)) {
      $("#results-status").textContent =
        message ||
        "Results could not be loaded. Choose Refresh results to try again.";
      resultManagerError(error);
    }
  } finally {
    context.loading = false;
    if (currentResults(context)) renderManagedResults(context);
  }
}
// Versions…: History and Results are two tabs; each tab is its own dialog underneath.
$("#results-manage").addEventListener("click", async () => {
  if (!state.recording || pendingResultMutations.has(state.recording.id))
    return;
  if ($("#history-dialog").open) $("#history-dialog").close();
  resultsContext = { ...recordingContext(), recording: state.recording };
  $("#results-dialog").showModal();
  await loadManagedResults(resultsContext);
});
$("#results-dialog").addEventListener("close", () => {
  resultsContext = null;
});
$("#results-refresh").addEventListener("click", async () => {
  if (!resultsContext || pendingResultMutations.has(resultsContext.id)) return;
  resultsContext = { ...recordingContext(), recording: state.recording };
  const context = resultsContext;
  await loadManagedResults(context);
  if (currentResults(context) && !state.dirty && !state.editing) {
    try {
      renderEditorDetails();
      renderTranscript();
    } catch (error) {
      if (currentResults(context)) resultManagerError(error);
    }
  }
});
$("#results-dialog").addEventListener("click", async (event) => {
  const button = event.target.closest(
    "[data-trash-result], [data-restore-result]",
  );
  const context = resultsContext;
  if (
    !button ||
    !context ||
    !currentResults(context) ||
    context.loading ||
    pendingResultMutations.has(context.id)
  )
    return;
  const restore = !!button.dataset.restoreResult;
  const id = button.dataset.restoreResult || button.dataset.trashResult;
  if (
    (state.dirty || state.editing) &&
    context.recording.revision !== state.recording.revision
  ) {
    resultManagerError(
      new Error(
        "The recording changed. Save or cancel your passage correction before managing results.",
      ),
    );
    return;
  }
  const changesActive =
    context.recording.active_transcript_id !== context.result ||
    (restore ? !context.result : id === context.result);
  if (
    changesActive &&
    state.dirty &&
    !confirm("Discard this unsaved passage correction?")
  )
    return;
  pendingResultMutations.add(context.id);
  $("#results-error").hidden = true;
  $("#results-status").textContent = restore
    ? "Restoring result…"
    : "Moving result to Trash…";
  renderManagedResults(context);
  const controls = [
    ...document.querySelectorAll(
      "#transcript textarea, #transcript button, #result-select, #compare-result, #results-manage",
    ),
  ];
  const disabled = controls.map((control) => control.disabled);
  controls.forEach((control) => {
    control.disabled = true;
  });
  let feedbackContext = context;
  let committed = false;
  try {
    const recording = await api(
      `/recordings/${context.id}/transcripts/${id}${restore ? "/restore" : ""}`,
      {
        method: restore ? "POST" : "DELETE",
        body: { revision: context.recording.revision },
      },
    );
    committed = true;
    const visible = currentResults(context);
    if (!sameRecordingView(context)) return;
    if (!acceptRecording(context, recording)) return;
    if (recording.active_transcript_id !== context.result) {
      clearEdit();
      state.replayEnd = null;
    }
    renderEditorDetails();
    if (!state.editing && !state.dirty) renderTranscript();
    if (visible) {
      resultsContext = { ...recordingContext(), recording };
      feedbackContext = resultsContext;
      await loadManagedResults(
        resultsContext,
        restore ? "Result restored." : "Result moved to Trash.",
        recording,
      );
    }
  } catch (error) {
    if (
      resultsContext === feedbackContext &&
      $("#results-dialog").open &&
      feedbackContext.epoch === viewEpoch &&
      feedbackContext.id === state.recording?.id
    ) {
      if (committed) {
        resultsContext = {
          ...recordingContext(),
          recording: state.recording,
          trash: feedbackContext.trash,
        };
        feedbackContext = resultsContext;
      }
      $("#results-status").textContent = committed
        ? "The result was updated. Refresh results to reload the view."
        : "No change was confirmed. Refresh results before trying again.";
      resultManagerError(error);
    }
  } finally {
    pendingResultMutations.delete(context.id);
    if (context.epoch === viewEpoch && context.id === state.recording?.id) {
      controls.forEach((control, index) => {
        if (control.isConnected) control.disabled = disabled[index];
      });
      renderResults();
      if (resultsContext && currentResults(resultsContext))
        renderManagedResults(resultsContext);
    }
  }
});
let resultMergeContext = null;
function currentResultMerge(context) {
  return (
    resultMergeContext === context &&
    $("#result-merge-dialog").open &&
    sameRecordingView(context)
  );
}
function renderResultMerge(context) {
  const preview = context.preview;
  const busy = context.loading || context.applying;
  $("#result-merge-refresh").disabled =
    busy || pendingResultMutations.has(context.id);
  $("#result-merge-apply").disabled =
    busy ||
    context.needsRefresh ||
    !preview?.summary.updated_passages ||
    pendingResultMutations.has(context.id);
  $("#result-merge-source").textContent =
    `From ${preview?.incoming_label || context.incomingLabel}`;
  $("#result-merge-summary").textContent = preview
    ? `Passages updated: ${preview.summary.updated_passages} · Timing: ${preview.summary.timing_updates} · Speakers: ${preview.summary.speaker_updates} · Conflicts: ${preview.summary.conflicts} · Skipped: ${preview.summary.skipped} · Unchanged: ${preview.summary.unchanged}`
    : "";
  $("#result-merge-passages").innerHTML = (preview?.passages || [])
    .map(
      (passage) =>
        `<article data-merge-passage="${h(passage.id)}" data-status="${h(passage.status)}"><strong>Passage ${h(passage.id)} · ${h(passage.status)}</strong><p>${h(passage.text)}</p>${passage.changes.length ? `<p class="field-note">Safe updates: ${h(passage.changes.map((change) => (change === "timing" ? "word timing" : "speaker labels")).join(", "))}</p>` : ""}${passage.reasons.length ? `<ul>${passage.reasons.map((reason) => `<li>${h(reason)}</li>`).join("")}</ul>` : ""}</article>`,
    )
    .join("");
  $("#result-merge-truncated").hidden = !preview?.truncated;
}
async function loadResultMerge(context) {
  if (
    !currentResultMerge(context) ||
    context.loading ||
    context.applying ||
    pendingResultMutations.has(context.id)
  )
    return;
  context.loading = true;
  context.needsRefresh = true;
  $("#result-merge-error").hidden = true;
  $("#result-merge-status").textContent = "Loading merge preview…";
  renderResultMerge(context);
  let requestingPreview = false;
  try {
    const current = await api(`/recordings/${context.id}`);
    if (!currentResultMerge(context)) return;
    if (
      current.active_transcript_id !== context.result ||
      current.revision < state.recording.revision
    )
      throw new Error(
        "The reviewed result changed in another window. Close this preview and reload the recording.",
      );
    requestingPreview = true;
    const preview = await api(
      `/recordings/${context.id}/transcripts/${context.result}/merge-preview`,
      {
        method: "POST",
        body: {
          incoming_result_id: context.incoming,
          revision: current.revision,
        },
      },
    );
    if (!currentResultMerge(context)) return;
    if (
      preview.result_id !== context.result ||
      preview.incoming_result_id !== context.incoming ||
      preview.revision !== current.revision ||
      !/^[a-f0-9]{64}$/.test(preview.fingerprint)
    )
      throw new Error("The merge preview does not match this reviewed result.");
    context.preview = preview;
    context.workingPreview = {
      recording: current,
      current: () =>
        currentResultMerge(context) &&
        !context.needsRefresh &&
        !context.loading &&
        context.preview === preview,
    };
    context.needsRefresh = false;
    $("#result-merge-status").textContent =
      "Preview only; your reviewed result has not changed.";
  } catch (error) {
    if (currentResultMerge(context)) {
      $("#result-merge-status").textContent =
        "Refresh the preview before applying updates.";
      $("#result-merge-error").textContent =
        requestingPreview && error.status === 404
          ? "Merge previews need the updated backend. Restart CBS Transcriber to use this feature."
          : error.message;
      $("#result-merge-error").hidden = false;
    }
  } finally {
    context.loading = false;
    if (currentResultMerge(context)) renderResultMerge(context);
  }
}
$("#results-dialog").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-preview-result-merge]");
  const manager = resultsContext;
  if (
    !button ||
    !manager ||
    !manager.result ||
    !currentResults(manager) ||
    manager.loading ||
    $("#result-merge-dialog").open ||
    pendingResultMutations.has(manager.id)
  )
    return;
  const incoming = manager.recording.transcripts.find(
    (result) => result.id === button.dataset.previewResultMerge,
  );
  if (
    !incoming ||
    incoming.trashed_at ||
    incoming.id === manager.result ||
    incoming.source.kind !== "enhancement" ||
    incoming.source.source_result_id !== manager.result
  )
    return;
  const context = {
    ...recordingContext(),
    manager,
    incoming: incoming.id,
    incomingLabel: incoming.label,
    preview: null,
    needsRefresh: true,
  };
  resultMergeContext = context;
  $("#result-merge-dialog").showModal();
  await loadResultMerge(context);
});
$("#result-merge-dialog").addEventListener("close", () => {
  resultMergeContext = null;
  pendingWorkingEditChoice = null;
});
$("#result-merge-refresh").addEventListener("click", () => {
  if (resultMergeContext) loadResultMerge(resultMergeContext);
});
$("#result-merge-apply").addEventListener("click", async () => {
  const context = resultMergeContext;
  if (
    !context ||
    !currentResultMerge(context) ||
    context.loading ||
    context.applying ||
    context.needsRefresh ||
    !context.preview?.summary.updated_passages ||
    pendingResultMutations.has(context.id) ||
    $("#save-status").textContent === "Saving…" ||
    !canDiscardPassage()
  )
    return;
  context.applying = true;
  pendingResultMutations.add(context.id);
  $("#result-merge-error").hidden = true;
  $("#result-merge-status").textContent = "Applying safe updates…";
  renderResultMerge(context);
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
    const result = await api(
      `/recordings/${context.id}/transcripts/${context.result}/merge`,
      {
        method: "POST",
        workingPreview: context.workingPreview,
        body: {
          incoming_result_id: context.incoming,
          revision: context.preview.revision,
          fingerprint: context.preview.fingerprint,
        },
      },
    );
    if (!sameRecordingView(context)) return;
    const recording = result.recording;
    if (!acceptRecording(context, recording)) return;
    clearEdit();
    state.replayEnd = null;
    renderEditorDetails();
    renderTranscript();
    if (resultMergeContext === context) $("#result-merge-dialog").close();
    if (resultsContext === context.manager && $("#results-dialog").open) {
      resultsContext = {
        ...recordingContext(),
        recording,
        trash: context.manager.trash,
      };
      $("#results-status").textContent =
        "Safe enhancement updates merged. Earlier versions remain in History.";
    }
    toast("Safe enhancement updates merged; reviewed wording was kept.");
  } catch (error) {
    if (currentResultMerge(context)) {
      context.needsRefresh = true;
      $("#result-merge-status").textContent =
        "No merge was confirmed. Refresh the preview before trying again.";
      $("#result-merge-error").textContent = error.message;
      $("#result-merge-error").hidden = false;
    }
  } finally {
    context.applying = false;
    pendingResultMutations.delete(context.id);
    if (currentResultMerge(context)) renderResultMerge(context);
    if (context.epoch === viewEpoch && context.id === state.recording?.id) {
      controls.forEach((control, index) => {
        if (control.isConnected) control.disabled = disabled[index];
      });
      renderResults();
      if (resultsContext && currentResults(resultsContext))
        renderManagedResults(resultsContext);
    }
  }
});
let historyContext = null,
  historyRequest = 0;
function currentHistory(context) {
  return (
    historyContext === context &&
    $("#history-dialog").open &&
    sameRecordingView(context)
  );
}
function historyAction(action) {
  return action === "version_restore"
    ? "restored earlier version"
    : action === "result_merge"
      ? "merged enhancement"
      : action === "wording_adopt"
        ? "adopted comparison wording"
        : String(action).replaceAll("_", " ");
}
function historyActor(kind) {
  return kind === "local_user"
    ? "Local user (identity unverified)"
    : historyAction(kind);
}
async function loadHistory(before = null) {
  const context = historyContext;
  if (!context || !currentHistory(context)) return;
  $("#history-status").textContent = "Loading saved versions…";
  $("#history-more").disabled = true;
  let versions;
  try {
    versions = await api(
      `/recordings/${context.id}/transcripts/${context.result}/versions?limit=25${before ? `&before_sequence=${before}` : ""}`,
    );
  } catch (error) {
    if (currentHistory(context)) {
      $("#history-status").textContent = error.message;
      $("#history-more").disabled = false;
    }
    return;
  }
  if (!currentHistory(context)) return;
  if (!before) context.head = versions[0]?.id;
  $("#history-versions").insertAdjacentHTML(
    "beforeend",
    versions
      .map(
        (version) =>
          `<button class="history-version button" data-history-version="${h(version.id)}"><strong>Version ${version.sequence} · ${h(historyAction(version.action))}${version.id === state.recording.result_roles?.working?.version_id ? " · Working transcript" : ""}</strong><span>${h(new Date(version.observed_at).toLocaleString("en-GB"))} · ${h(historyActor(version.actor_kind))}</span><code>${h(version.payload_sha256)}</code></button>`,
      )
      .join(""),
  );
  const last = versions.at(-1);
  $("#history-more").hidden = versions.length < 25;
  $("#history-more").dataset.before = last?.sequence || "";
  $("#history-more").disabled = false;
  $("#history-status").textContent = versions.length
    ? "Choose a version to inspect its exact saved text."
    : before
      ? "No earlier versions."
      : "No saved versions.";
}
$("#history-open").addEventListener(
  "click",
  handle(async () => {
    if (!state.recording) return;
    // Without an open result (all in Trash), Versions opens on its Results tab.
    if (!state.recording.active_transcript_id) {
      $("#results-manage").click();
      return;
    }
    historyContext = recordingContext();
    historyRequest++;
    $("#history-versions").replaceChildren();
    $("#history-preview").hidden = true;
    $("#history-more").hidden = true;
    $("#history-dialog").showModal();
    await loadHistory();
  }),
);
$("#history-dialog").addEventListener("close", () => {
  pendingWorkingEditChoice = null;
  historyContext = null;
  historyRequest++;
  workingRequest++;
});
$("#versions-history-tab").addEventListener("click", () => {
  $("#results-dialog").close();
  $("#history-open").click();
});
$("#history-more").addEventListener(
  "click",
  handle(() => loadHistory($("#history-more").dataset.before)),
);
$("#history-versions").addEventListener(
  "click",
  handle(async (event) => {
    const button = event.target.closest("[data-history-version]");
    const context = historyContext;
    if (!button || !context || !currentHistory(context)) return;
    const request = ++historyRequest;
    $("#history-preview").hidden = true;
    $("#history-status").textContent = "Loading exact saved text…";
    const path = `/recordings/${context.id}/transcripts/${context.result}/versions/${button.dataset.historyVersion}`;
    let version;
    try {
      version = await api(path);
    } catch (error) {
      if (currentHistory(context) && request === historyRequest)
        $("#history-status").textContent = error.message;
      return;
    }
    if (!currentHistory(context) || request !== historyRequest) return;
    $("#history-preview-title").textContent =
      `Version ${version.sequence} · ${historyAction(version.action)}`;
    $("#history-preview-meta").textContent =
      `${new Date(version.observed_at).toLocaleString("en-GB")} · ${historyActor(version.actor_kind)}${version.action === "legacy_observed" ? " · Earlier history unknown" : ""}`;
    $("#history-hash").textContent = `SHA-256 ${version.payload_sha256}`;
    $("#history-text").replaceChildren();
    for (const segment of version.payload.segments) {
      const passage = document.createElement("p");
      const speaker = document.createElement("strong");
      speaker.textContent = `${speakerName(version.payload, segment.speaker) || "Speaker"} · ${time(segment.start)}`;
      const text = document.createElement("span");
      text.textContent = segment.text;
      passage.append(speaker, text);
      $("#history-text").append(passage);
    }
    $("#history-export").href = CBSLibraries.resource(
      `/api${path}/export?format=text`,
    );
    // The newest version is the current text; any other can be restored as a new version.
    $("#history-restore").hidden = version.id === context.head;
    $("#history-restore").dataset.version = version.id;
    $("#history-restore").dataset.sequence = version.sequence;
    $("#history-mark-working").dataset.version = version.id;
    $("#history-mark-working").disabled =
      version.id === state.recording.result_roles?.working?.version_id;
    $("#history-preview").hidden = false;
    $("#history-status").textContent =
      "Historical preview only; the reviewed result is unchanged.";
  }),
);
let workingContext = null,
  workingRequest = 0;
async function previewWorkingVersion(
  versionId,
  historySource = null,
  token = null,
) {
  const request = token ?? ++workingRequest;
  const context = {
    ...recordingContext(),
    revision: state.recording.revision,
    roleRevision: state.recording.result_roles?.revision || 0,
    versionId,
  };
  const preview = await api(`/recordings/${context.id}/working-preview`, {
    method: "POST",
    body: {
      version_id: versionId,
      revision: context.revision,
      role_revision: context.roleRevision,
    },
  });
  if (
    request !== workingRequest ||
    !sameRecordingView(context) ||
    (historySource &&
      (!currentHistory(historySource) ||
        $("#history-mark-working").dataset.version !== versionId))
  )
    return;
  workingContext = { ...context, preview };
  $("#working-version-preview").textContent =
    `${preview.label} · Version ${preview.next.sequence}${preview.previous ? ` replaces Working version ${preview.previous.sequence}.` : "."} The review view and current text stay unchanged.`;
  $("#working-version-hash").textContent =
    `SHA-256 ${preview.next.payload_sha256}`;
  $("#working-version-error").hidden = true;
  $("#working-version-apply").disabled = false;
  $("#working-version-dialog").showModal();
}
$("#working-mark-current").addEventListener(
  "click",
  handle(async () => {
    const request = ++workingRequest;
    const context = recordingContext();
    const versions = await api(
      `/recordings/${context.id}/transcripts/${context.result}/versions?limit=1`,
    );
    if (request === workingRequest && sameRecordingView(context) && versions[0])
      await previewWorkingVersion(versions[0].id, null, request);
  }),
);
$("#history-mark-working").addEventListener(
  "click",
  handle(async (event) => {
    if (historyContext && currentHistory(historyContext))
      await previewWorkingVersion(
        event.currentTarget.dataset.version,
        historyContext,
      );
  }),
);
$("#working-version-dialog").addEventListener("close", () => {
  workingContext = null;
  workingRequest++;
});
$("#working-version-apply").addEventListener("click", async () => {
  const context = workingContext;
  if (
    !context ||
    !sameRecordingView(context) ||
    pendingResultMutations.has(context.id)
  )
    return;
  pendingResultMutations.add(context.id);
  $("#working-version-apply").disabled = true;
  try {
    const recording = await api(`/recordings/${context.id}/working`, {
      method: "POST",
      body: {
        version_id: context.versionId,
        revision: context.revision,
        role_revision: context.roleRevision,
        fingerprint: context.preview.fingerprint,
      },
    });
    if (!sameRecordingView(context)) return;
    // Role updates do not replace a pending passage/notes draft or change the review view.
    state.recording.result_roles = recording.result_roles;
    renderResults();
    if (historyContext && currentHistory(historyContext)) {
      $("#history-versions").replaceChildren();
      await loadHistory();
      $("#history-mark-working").disabled = true;
    }
    $("#working-version-dialog").close();
    toast("Exact saved version marked Working.");
  } catch (error) {
    if (workingContext === context) {
      $("#working-version-error").textContent =
        `${error.message} Close this preview and try again.`;
      $("#working-version-error").hidden = false;
    }
  } finally {
    pendingResultMutations.delete(context.id);
    if (sameRecordingView(context)) renderResults();
  }
});
$("#history-restore").addEventListener(
  "click",
  handle(async (event) => {
    const button = event.currentTarget,
      context = historyContext,
      { version, sequence } = button.dataset;
    // Restore replaces only the passage text; notes and project settings stay as they are.
    if (!context || !currentHistory(context) || !canDiscardPassage()) return;
    if (
      !confirm(
        `Restore version ${sequence} as the newest version? The current text stays in History.`,
      )
    )
      return;
    button.disabled = true;
    let recording;
    try {
      recording = await api(
        `/recordings/${context.id}/transcripts/${context.result}/versions/${version}/restore`,
        { method: "POST", body: { revision: state.recording.revision } },
      );
    } catch (error) {
      // Show refusals (e.g. a newer edit elsewhere) inside the open History dialog.
      if (error.name !== "AbortError" && currentHistory(context))
        $("#history-status").textContent = error.message;
      return;
    } finally {
      button.disabled = false;
    }
    if (!sameRecordingView(context)) return;
    if (!acceptRecording(context, recording)) return;
    clearEdit();
    $("#history-dialog").close();
    renderEditorDetails();
    renderTranscript();
    toast(
      `Version ${sequence} restored as the newest version; earlier text stays in History.`,
    );
  }),
);
let researchReportContext = null;
function renderReportPreview() {
  $("#research-report-preview").textContent = $("#research-report-text").checked
    ? "ZIP includes the manifest, methods draft and readable text for pinned Working versions. Transcript text and speaker names are included. Unassigned recordings are omitted."
    : "ZIP includes the manifest and methods draft, without transcript text, speaker names, notes, filenames or project names.";
}
$("#research-report-open").addEventListener("click", () => {
  if (!state.project || !featureOn("research_report")) return;
  researchReportContext = { id: state.project.id, epoch: viewEpoch };
  $("#research-report-text").checked = false;
  $("#research-report-text").disabled = false;
  $("#research-report-status").textContent = "";
  $("#research-report-download").disabled = false;
  renderReportPreview();
  $("#research-report-dialog").showModal();
});
$("#research-report-text").addEventListener("change", renderReportPreview);
$("#research-report-dialog").addEventListener("close", () => {
  researchReportContext = null;
});
$("#research-report-download").addEventListener(
  "click",
  handle(async () => {
    const context = researchReportContext;
    if (
      !context ||
      context.epoch !== viewEpoch ||
      context.id !== state.project?.id
    )
      return;
    const button = $("#research-report-download");
    button.disabled = true;
    $("#research-report-text").disabled = true;
    $("#research-report-status").textContent = "Preparing local report…";
    const current = () =>
      researchReportContext === context &&
      context.epoch === viewEpoch &&
      context.id === state.project?.id &&
      $("#research-report-dialog").open;
    try {
      const response = await fetch(
        `/api/projects/${context.id}/research-report`,
        {
          method: "POST",
          headers: {
            "X-CBS-Request": "1",
            "Content-Type": "application/json",
            ...CBSLibraries.instanceHeaders(),
          },
          body: JSON.stringify({
            include_selected_transcripts: $("#research-report-text").checked,
            // The visible collection limits the report (ADR 0060).
            ...(state.collection ? { collection: state.collection } : {}),
          }),
        },
      );
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        CBSLibraries.stale(error);
        throw new Error(
          typeof error.detail === "string"
            ? error.detail
            : "Report creation failed.",
        );
      }
      const blob = await response.blob();
      if (!current()) return;
      const url = URL.createObjectURL(blob),
        link = document.createElement("a");
      link.href = url;
      link.download = "project-research-report.zip";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      $("#research-report-status").textContent =
        "Report downloaded. Review its contents and evidence gaps before sharing.";
    } catch (error) {
      if (current()) $("#research-report-status").textContent = error.message;
    } finally {
      if (current()) {
        button.disabled = false;
        $("#research-report-text").disabled = false;
      }
    }
  }),
);
let annotationView = null;
function currentAnnotations(context) {
  return (
    annotationView === context &&
    $("#annotations-dialog").open &&
    context.project === state.project?.id &&
    sameRecordingView(context)
  );
}
async function annotationWork(action) {
  const context = annotationView;
  if (!context || !currentAnnotations(context)) return;
  $("#annotation-fields").disabled = true;
  try {
    await action(context);
  } catch (error) {
    if (currentAnnotations(context))
      $("#annotation-status").textContent = error.message;
  } finally {
    if (currentAnnotations(context)) $("#annotation-fields").disabled = false;
  }
}
function annotationRoot(context) {
  return `/recordings/${context.id}/annotation-runs`;
}
async function annotationMutation(context, path, method, body) {
  const identity = JSON.stringify({ path, method, body });
  if (context.pendingRequest?.identity !== identity)
    context.pendingRequest = { identity, id: crypto.randomUUID() };
  const result = await api(path, {
    method,
    body: { ...body, request_id: context.pendingRequest.id },
  });
  return result;
}
function codebookTopic(topic = {}) {
  const row = document.createElement("article");
  row.className = "codebook-topic";
  row.dataset.topicId = topic.id || `topic-${crypto.randomUUID()}`;
  row.innerHTML = `<label>Topic name<input data-codebook-field="name" maxlength="200"></label><label>Definition<textarea data-codebook-field="definition" maxlength="4000"></textarea></label><details><summary>Coding rules and examples</summary>${["include", "exclude", "examples"].map((key) => `<label>${h({ include: "Include when", exclude: "Exclude when", examples: "Examples" }[key])} (one per line)<textarea data-codebook-field="${key}"></textarea></label>`).join("")}<small>Stable topic ID: ${h(row.dataset.topicId)}</small></details><label class="checkbox-label"><input type="checkbox" data-codebook-field="retired" ${topic.retired ? "checked" : ""}>Retired</label>`;
  row.originalTopic = topic;
  for (const key of ["name", "definition", "include", "exclude", "examples"]) {
    const input = row.querySelector(`[data-codebook-field="${key}"]`);
    input.value = Array.isArray(topic[key])
      ? topic[key].join("\n")
      : topic[key] || "";
    input.defaultValue = input.value;
  }
  $("#codebook-topics").append(row);
}
function renderCodebook(context) {
  const version = context.codebook;
  $("#codebook-summary").textContent = version
    ? `· version ${version.sequence} · ${version.payload.approved ? "Approved" : "Draft"}`
    : "· Not yet saved";
  $("#codebook-topics").replaceChildren();
  (version?.payload.topics || [{}]).forEach(codebookTopic);
  $("#codebook-approved").checked = !!version?.payload.approved;
  $("#annotation-run-new").disabled =
    !version?.payload.approved || !context.head;
  if (!version) $("#codebook-editor").open = true;
}
function codebookPayload() {
  return {
    format: "cbs-topic-codebook-v1",
    approved: $("#codebook-approved").checked,
    topics: [...$("#codebook-topics").children].map((row) => {
      const fields = Object.fromEntries(
        [...row.querySelectorAll("[data-codebook-field]")].map((input) => [
          input.dataset.codebookField,
          input,
        ]),
      );
      const unchanged = (key) =>
        fields[key].value === fields[key].defaultValue &&
        row.originalTopic[key] !== undefined;
      return {
        id: row.dataset.topicId,
        name: unchanged("name") ? row.originalTopic.name : fields.name.value,
        definition: unchanged("definition")
          ? row.originalTopic.definition
          : fields.definition.value,
        ...Object.fromEntries(
          ["include", "exclude", "examples"].map((key) => [
            key,
            unchanged(key)
              ? row.originalTopic[key]
              : fields[key].value.split("\n").filter((line) => line.length),
          ]),
        ),
        retired: fields.retired.checked,
      };
    }),
  };
}
function renderAnnotationRuns(context) {
  $("#annotation-run").innerHTML =
    '<option value="">Choose a coding run…</option>' +
    context.runs
      .map(
        (run) =>
          `<option value="${h(run.id)}">Run ${run.sequence} · ${h(historyAction(run.kind))} · ${run.item_count} annotations</option>`,
      )
      .join("");
  $("#annotation-run").value = context.run?.id || "";
  $("#annotation-runs-more").hidden = !context.moreRuns;
}
async function openAnnotations() {
  if (!featureOn("topics")) return;
  const context = {
    ...recordingContext(),
    project: state.project.id,
    run: null,
    item: null,
    evidence: [],
    occurrences: [],
  };
  annotationView = context;
  $("#annotation-status").textContent = "Loading local coding records…";
  $("#annotation-coding").hidden = true;
  $("#annotation-recording").hidden = !context.id;
  $("#codebook-topics").replaceChildren();
  $("#codebook-editor").open = false;
  $("#annotations-dialog").showModal();
  await annotationWork(async () => {
    const [books, runs, heads] = await Promise.all([
      api(`/projects/${context.project}/codebook/versions?limit=1`),
      context.id ? api(`${annotationRoot(context)}?limit=100`) : [],
      context.id && context.result
        ? api(
            `/recordings/${context.id}/transcripts/${context.result}/versions?limit=1`,
          )
        : [],
    ]);
    const codebook = books[0]
      ? await api(
          `/projects/${context.project}/codebook/versions/${books[0].id}`,
        )
      : null;
    if (!currentAnnotations(context)) return;
    Object.assign(context, {
      codebook,
      runs,
      moreRuns: runs.length === 100,
      head: heads[0] || null,
    });
    renderCodebook(context);
    renderAnnotationRuns(context);
    $("#annotation-source-status").textContent = context.head
      ? `New runs use saved transcript version ${context.head.sequence}. Unsaved transcript corrections are excluded.`
      : "Open a recording with a saved transcript to code evidence.";
    $("#annotation-status").textContent =
      "Local prototype · Define an approved codebook, then create or select a coding run.";
  });
}
$("#topics-open").addEventListener("click", handle(openAnnotations));
$("#project-topics-open").addEventListener("click", handle(openAnnotations));
$("#annotations-dialog").addEventListener("close", () => {
  annotationView = null;
});
$("#codebook-add").addEventListener("click", () => codebookTopic());
$("#codebook-save").addEventListener("click", () =>
  annotationWork(async (context) => {
    const version = await api(
      `/projects/${context.project}/codebook/versions`,
      {
        method: "POST",
        body: {
          payload: codebookPayload(),
          revision: context.codebook?.sequence || 0,
        },
      },
    );
    if (!currentAnnotations(context)) return;
    context.codebook = version;
    renderCodebook(context);
    renderAnnotationSourceStatus(context);
    $("#annotation-status").textContent =
      `Codebook version ${version.sequence} saved.`;
  }),
);
function renderAnnotationSourceStatus(context) {
  if (!context.run) return;
  const run = context.run;
  const older = [
    run.source_stale
      ? "Based on an earlier transcript version"
      : "Frozen saved transcript version",
    run.result_id !== context.result ? "Another saved result" : "",
    run.codebook_stale || run.codebook_version_id !== context.codebook?.id
      ? "Earlier codebook version"
      : "",
    run.kind === "imported_suggestions"
      ? "Imported producer declarations are unverified"
      : "",
  ].filter(Boolean);
  $("#annotation-source-status").textContent =
    `${older.join(" · ")}. Source and codebook are fixed for this run; coding never rewrites transcript text.`;
}
async function loadAnnotationRun(context, id) {
  if (!id) {
    context.run = null;
    $("#annotation-coding").hidden = true;
    return;
  }
  const run = await api(`${annotationRoot(context)}/${id}`);
  const [source, book, items] = await Promise.all([
    api(
      `/recordings/${context.id}/transcripts/${run.result_id}/versions/${run.source_version_id}`,
    ),
    api(
      `/projects/${context.project}/codebook/versions/${run.codebook_version_id}`,
    ),
    api(`${annotationRoot(context)}/${id}/items?limit=100`),
  ]);
  if (!currentAnnotations(context)) return;
  Object.assign(context, {
    run,
    source,
    runBook: book,
    items,
    moreItems: items.length === 100,
    item: null,
    evidence: [],
  });
  renderAnnotationRuns(context);
  renderAnnotationSourceStatus(context);
  $("#annotation-coding").hidden = false;
  $("#annotation-passage").innerHTML = source.payload.segments
    .filter((passage) => run.passage_ids.includes(passage.id))
    .map(
      (passage) =>
        `<option value="${h(passage.id)}">Passage ${h(passage.id)} · ${time(passage.start)} · ${h(speakerName(source.payload, passage.speaker))}</option>`,
    )
    .join("");
  $("#annotation-topic").innerHTML = book.payload.topics
    .filter((topic) => !topic.retired)
    .map((topic) => `<option value="${h(topic.id)}">${h(topic.name)}</option>`)
    .join("");
  $("#annotation-export").href = CBSLibraries.resource(
    `/api${annotationRoot(context)}/${run.id}/export`,
  );
  $("#annotation-import-template").textContent = JSON.stringify(
    {
      format: "cbs-topic-suggestions-v1",
      result_id: run.result_id,
      source_version_id: run.source_version_id,
      source_payload_sha256: run.source_payload_sha256,
      codebook_version_id: run.codebook_version_id,
      codebook_payload_sha256: run.codebook_payload_sha256,
      passage_ids: run.passage_ids,
      producer: { model: "declared-model-id", revision: "declared-revision" },
      suggestions: [],
    },
    null,
    2,
  );
  resetAnnotationDraft(context);
  renderAnnotationItems(context);
}
$("#annotation-run").addEventListener("change", (event) =>
  annotationWork((context) => loadAnnotationRun(context, event.target.value)),
);
$("#annotation-run-new").addEventListener("click", () =>
  annotationWork(async (context) => {
    if (!context.codebook?.payload.approved || !context.head)
      throw new Error("Save an approved codebook and a transcript first.");
    const run = await annotationMutation(
      context,
      annotationRoot(context),
      "POST",
      {
        result_id: context.result,
        source_version_id: context.head.id,
        codebook_version_id: context.codebook.id,
      },
    );
    if (!currentAnnotations(context)) return;
    context.runs = [
      run,
      ...context.runs.filter((saved) => saved.id !== run.id),
    ];
    await loadAnnotationRun(context, run.id);
    if (currentAnnotations(context)) {
      context.pendingRequest = null;
      $("#annotation-status").textContent =
        "Manual coding run created on the frozen saved version.";
    }
  }),
);
function selectedAnnotationPassage(context) {
  return context.source?.payload.segments.find(
    (passage) => passage.id === $("#annotation-passage").value,
  );
}
function evidenceOccurrences(text, quote) {
  if (!quote) return [];
  const boundaries = new Map();
  let offset = 0;
  Array.from(text).forEach((point, index) => {
    boundaries.set(offset, index);
    offset += point.length;
  });
  boundaries.set(offset, Array.from(text).length);
  const matches = [];
  for (
    let position = text.indexOf(quote);
    position !== -1 && matches.length < 100;
    position = text.indexOf(quote, position + 1)
  ) {
    if (boundaries.has(position) && boundaries.has(position + quote.length))
      matches.push({
        start_char: boundaries.get(position),
        end_char: boundaries.get(position + quote.length),
        exact_text: quote,
      });
  }
  return matches;
}
function annotationOccurrences() {
  const context = annotationView,
    passage = selectedAnnotationPassage(context);
  context.occurrences = passage
    ? evidenceOccurrences(passage.text, $("#annotation-quote").value)
    : [];
  $("#annotation-occurrence").innerHTML = context.occurrences
    .map(
      (span, index) =>
        `<option value="${index}">Occurrence ${index + 1} · code points ${span.start_char}–${span.end_char}</option>`,
    )
    .join("");
  $("#annotation-evidence-status").textContent = context.occurrences.length
    ? `${context.occurrences.length} exact occurrences${context.occurrences.length === 100 ? " (first 100; use a longer quote)" : ""}. Choose the intended occurrence.`
    : "No exact occurrence. Quotes must match the frozen source literally.";
  $("#annotation-evidence-add").disabled = !context.occurrences.length;
  renderAnnotationEvidence(context);
}
function selectedAnnotationEvidence(context) {
  const occurrence =
    context.occurrences[Number($("#annotation-occurrence").value)];
  return occurrence
    ? { passage_id: $("#annotation-passage").value, ...occurrence }
    : null;
}
function renderAnnotationEvidence(context) {
  const passage = selectedAnnotationPassage(context);
  $("#annotation-seek").disabled =
    !passage ||
    passage.start == null ||
    passage.end == null ||
    passage.end <= passage.start;
  if (!passage) {
    $("#annotation-source-text").textContent = "No passage in this run.";
    return;
  }
  const points = Array.from(passage.text),
    current = selectedAnnotationEvidence(context);
  const spans = [...context.evidence, ...(current ? [current] : [])]
    .filter((span) => span.passage_id === passage.id)
    .sort((a, b) => a.start_char - b.start_char);
  let end = 0;
  const fragment = document.createDocumentFragment();
  for (const span of spans) {
    if (span.end_char <= end) continue;
    const start = Math.max(end, span.start_char);
    fragment.append(document.createTextNode(points.slice(end, start).join("")));
    const mark = document.createElement("mark");
    mark.textContent = points.slice(start, span.end_char).join("");
    fragment.append(mark);
    end = span.end_char;
  }
  fragment.append(document.createTextNode(points.slice(end).join("")));
  $("#annotation-source-text").replaceChildren(fragment);
  $("#annotation-evidence-list").replaceChildren(
    ...context.evidence.map((span, index) => {
      const row = document.createElement("p"),
        label = document.createElement("span"),
        button = document.createElement("button");
      label.textContent = `Passage ${span.passage_id} · ${span.start_char}–${span.end_char}: ${span.exact_text}`;
      button.type = "button";
      button.className = "text-button";
      button.dataset.removeEvidence = index;
      button.setAttribute("aria-label", `Remove evidence ${index + 1}`);
      button.textContent = "Remove";
      row.append(label, document.createTextNode(" "), button);
      return row;
    }),
  );
}
$("#annotation-passage").addEventListener("change", () => {
  $("#annotation-quote").value = "";
  annotationOccurrences();
});
$("#annotation-quote").addEventListener("input", annotationOccurrences);
$("#annotation-occurrence").addEventListener("change", () =>
  renderAnnotationEvidence(annotationView),
);
$("#annotation-evidence-add").addEventListener("click", () => {
  const context = annotationView,
    span = selectedAnnotationEvidence(context);
  if (
    span &&
    !context.evidence.some(
      (value) => JSON.stringify(value) === JSON.stringify(span),
    )
  )
    context.evidence.push(span);
  renderAnnotationEvidence(context);
});
$("#annotation-evidence-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-evidence]");
  if (button) {
    annotationView.evidence.splice(Number(button.dataset.removeEvidence), 1);
    renderAnnotationEvidence(annotationView);
  }
});
$("#annotation-seek").addEventListener("click", () => {
  const context = annotationView,
    passage = selectedAnnotationPassage(context);
  if (
    currentAnnotations(context) &&
    passage?.start != null &&
    passage.end > passage.start
  )
    seekTo(passage.start);
});
function resetAnnotationDraft(context) {
  context.item = null;
  context.evidence = [];
  $("#annotation-review").hidden = true;
  $("#annotation-original").hidden = true;
  $("#annotation-add").disabled = false;
  $("#annotation-item").value = "";
  $("#annotation-quote").value = "";
  $("#annotation-uncertainty").value = "";
  $("#annotation-review-reason").value = "";
  annotationOccurrences();
}
$("#annotation-clear").addEventListener("click", () =>
  resetAnnotationDraft(annotationView),
);
function annotationPayload(context) {
  const evidence = context.evidence.length
    ? context.evidence
    : [selectedAnnotationEvidence(context)].filter(Boolean);
  if (!evidence.length)
    throw new Error("Select at least one exact evidence quote.");
  const payload = {
    topic_id: $("#annotation-topic").value,
    polarity: $("#annotation-polarity").value,
    attribution: $("#annotation-attribution").value,
    evidence,
  };
  if ($("#annotation-uncertainty").value)
    payload.uncertainty_reason = $("#annotation-uncertainty").value;
  if (payload.polarity === "uncertain" && !payload.uncertainty_reason?.trim())
    throw new Error("Explain why this evaluation is uncertain.");
  return payload;
}
function renderAnnotationItems(context) {
  $("#annotation-item").innerHTML =
    '<option value="">Choose an annotation…</option>' +
    context.items
      .map(
        (item) =>
          `<option value="${h(item.id)}">Annotation ${item.sequence} · ${h(item.polarity)} · ${h(item.review_state)} · revision ${item.revision}</option>`,
      )
      .join("");
  $("#annotation-item").value = context.item?.id || "";
  $("#annotation-items-more").hidden = !context.moreItems;
}
$("#annotation-runs-more").addEventListener("click", () =>
  annotationWork(async (context) => {
    const runs = await api(
      `${annotationRoot(context)}?limit=100&before_sequence=${context.runs.at(-1).sequence}`,
    );
    if (!currentAnnotations(context)) return;
    context.runs.push(...runs);
    context.moreRuns = runs.length === 100;
    renderAnnotationRuns(context);
  }),
);
$("#annotation-items-more").addEventListener("click", () =>
  annotationWork(async (context) => {
    const items = await api(
      `${annotationRoot(context)}/${context.run.id}/items?limit=100&before_sequence=${context.items.at(-1).sequence}`,
    );
    if (!currentAnnotations(context)) return;
    context.items.push(...items);
    context.moreItems = items.length === 100;
    renderAnnotationItems(context);
  }),
);
async function loadAnnotationItem(context, id) {
  if (!id) {
    resetAnnotationDraft(context);
    return;
  }
  const item = await api(
    `${annotationRoot(context)}/${context.run.id}/items/${id}`,
  );
  if (!currentAnnotations(context)) return;
  context.item = item;
  $("#annotation-add").disabled = true;
  const payload = item.current || item.suggestion;
  context.evidence = structuredClone(payload.evidence);
  $("#annotation-topic").value = payload.topic_id;
  $("#annotation-polarity").value = payload.polarity;
  $("#annotation-attribution").value = payload.attribution;
  $("#annotation-uncertainty").value = payload.uncertainty_reason || "";
  $("#annotation-review-reason").value = "";
  $("#annotation-passage").value = payload.evidence[0].passage_id;
  $("#annotation-quote").value = payload.evidence[0].exact_text;
  annotationOccurrences();
  $("#annotation-occurrence").value = String(
    context.occurrences.findIndex(
      (span) => span.start_char === payload.evidence[0].start_char,
    ),
  );
  renderAnnotationEvidence(context);
  $("#annotation-review").hidden = false;
  $("#annotation-item-status").textContent =
    `${historyAction(item.origin)} · ${item.review_state} · review revision ${item.revision}. Speaker labels describe source voices, not verified identities.`;
  $("#annotation-review-evidence").textContent = payload.evidence
    .map((span) => `Passage ${span.passage_id}: ${span.exact_text}`)
    .join("\n");
  $("#annotation-original").hidden = !item.suggestion;
  $("#annotation-original").open = false;
  $("#annotation-original-text").textContent = item.suggestion
    ? JSON.stringify(item.suggestion, null, 2)
    : "";
  $("#annotation-item").value = item.id;
}
$("#annotation-item").addEventListener("change", (event) =>
  annotationWork((context) => loadAnnotationItem(context, event.target.value)),
);
async function refreshAnnotationItems(context, id) {
  const [items, run] = await Promise.all([
    api(`${annotationRoot(context)}/${context.run.id}/items?limit=100`),
    api(`${annotationRoot(context)}/${context.run.id}`),
  ]);
  if (!currentAnnotations(context)) return;
  context.run = run;
  context.runs = context.runs.map((saved) =>
    saved.id === run.id ? run : saved,
  );
  renderAnnotationRuns(context);
  renderAnnotationSourceStatus(context);
  const previous = context.items || [];
  context.items = [
    ...new Map([...previous, ...items].map((item) => [item.id, item])).values(),
  ].sort((left, right) => right.sequence - left.sequence);
  if (!previous.length) context.moreItems = items.length === 100;
  renderAnnotationItems(context);
  await loadAnnotationItem(context, id);
}
$("#annotation-add").addEventListener("click", () =>
  annotationWork(async (context) => {
    const item = await annotationMutation(
      context,
      `${annotationRoot(context)}/${context.run.id}/items`,
      "POST",
      { payload: annotationPayload(context) },
    );
    if (!currentAnnotations(context)) return;
    await refreshAnnotationItems(context, item.id);
    if (currentAnnotations(context)) {
      context.pendingRequest = null;
      $("#annotation-status").textContent =
        "Manual annotation saved with an accepted human review.";
    }
  }),
);
$("#annotation-review").addEventListener("click", (event) => {
  const action = event.target.closest("[data-annotation-review]")?.dataset
    .annotationReview;
  if (!action) return;
  annotationWork(async (context) => {
    const item = await annotationMutation(
      context,
      `${annotationRoot(context)}/${context.run.id}/items/${context.item.id}`,
      "PATCH",
      {
        revision: context.item.revision,
        action,
        ...(action === "change" ? { payload: annotationPayload(context) } : {}),
        ...($("#annotation-review-reason").value
          ? { reason: $("#annotation-review-reason").value }
          : {}),
      },
    );
    if (!currentAnnotations(context)) return;
    await refreshAnnotationItems(context, item.id);
    if (currentAnnotations(context)) {
      context.pendingRequest = null;
      $("#annotation-status").textContent =
        `Review ${action} saved at revision ${item.revision}.`;
    }
  });
});
$("#annotation-import").addEventListener("click", () =>
  annotationWork(async (context) => {
    const file = $("#annotation-import-file").files[0];
    if (!file || file.size > 2 * 1024 * 1024)
      throw new Error("Choose a suggestion JSON file no larger than 2 MiB.");
    const raw = await file.text();
    if (!currentAnnotations(context)) return;
    const identity = `${annotationRoot(context)}/import:${raw}`;
    if (context.pendingRequest?.identity !== identity)
      context.pendingRequest = { identity, id: crypto.randomUUID() };
    const run = await api(`${annotationRoot(context)}/import`, {
      method: "POST",
      body: `{"request_id":${JSON.stringify(context.pendingRequest.id)},"bundle":${raw}}`,
    });
    if (!currentAnnotations(context)) return;
    context.runs = [
      run,
      ...context.runs.filter((saved) => saved.id !== run.id),
    ];
    await loadAnnotationRun(context, run.id);
    if (currentAnnotations(context)) {
      context.pendingRequest = null;
      $("#annotation-status").textContent =
        "Suggestions imported into a separate run. They are unreviewed; producer declarations are unverified.";
    }
  }),
);
$("#compare-result").addEventListener(
  "change",
  handle(async (event) => {
    if (!canDiscardPassage()) {
      event.target.value = state.comparison?.id || "";
      return;
    }
    const id = event.target.value,
      context = recordingContext();
    const result = id
      ? await api(`/recordings/${context.id}/transcripts/${id}`)
      : null;
    if (!sameRecordingView(context) || $("#compare-result").value !== id)
      return;
    clearEdit();
    state.comparison = result;
    renderTranscript();
    renderComparison();
  }),
);
function renderComparison() {
  alignmentToken++;
  clearComparisonAlignment();
  const result = state.comparison;
  $("#comparison-panel").hidden = !result;
  $("#editor-layout").classList.toggle("comparing", !!result);
  $("#transcript-search").placeholder = result
    ? "Find in both transcripts…"
    : "Find in transcript…";
  $("#transcript-search").setAttribute(
    "aria-label",
    result ? "Search both transcripts" : "Search transcript",
  );
  activeComparePassage = activeCompareWord = null;
  schedulePlaybackViewport();
  if (!result) return;
  const expectedTiming = expectsWordTiming(result);
  renderTimingWarning(result, "#compare-timing-warning");
  $("#comparison-info").textContent =
    `${resultLabel(result)} · Read only; choose Review result to edit.` +
    untimedNote(result);
  const speakers = [...new Set(result.segments.map((s) => s.speaker))];
  $("#comparison-transcript").innerHTML = result.segments
    .map(
      (segment) =>
        `<article class="passage ${timingGap(segment, expectedTiming) ? "timing-gap" : ""}" id="compare-passage-${segment.id}" data-compare-segment="${segment.id}"><div class="passage-heading"><span class="speaker-badge s${speakers.indexOf(segment.speaker) % 4}" title="${h(speakerName(result, segment.speaker))}">${h(speakerName(result, segment.speaker))}</span><button class="timestamp" ${segment.start == null ? "disabled" : ""}>${time(segment.start)}</button>${timingTag(segment, expectedTiming)}</div><p class="passage-text"></p>${timingNote(segment, expectedTiming)}</article>`,
    )
    .join("");
  for (const segment of result.segments)
    renderPassageText(
      document
        .getElementById(`compare-passage-${segment.id}`)
        .querySelector(".passage-text"),
      segment,
      false,
    );
  highlightSearch(result.segments, "compare-passage-");
  refreshPassageRepairs();
  refreshComparisonAlignment();
  schedulePlaybackViewport();
}
let alignmentToken = 0,
  alignmentCache = null,
  comparisonAligned = false,
  alignmentFrame = null;
function comparisonKey() {
  return [
    state.recording?.id,
    state.recording?.active_transcript_id,
    state.recording?.revision,
    state.comparison?.id,
    state.comparison?.updated,
  ].join(":");
}
const alignmentObserver = new ResizeObserver(() => scheduleAlignmentLayout());
function clearWordingMarks(node, selector) {
  const parents = new Set();
  node.querySelectorAll(selector).forEach((mark) => {
    const parent = mark.parentNode;
    mark.replaceWith(...mark.childNodes);
    parents.add(parent);
  });
  parents.forEach((parent) => parent.normalize());
}
function clearComparisonAlignment() {
  comparisonAligned = false;
  alignmentObserver.disconnect();
  for (const pane of [$("#transcript"), $("#comparison-transcript")]) {
    pane
      .querySelectorAll("[data-use-wording]")
      .forEach((button) => button.remove());
    clearWordingMarks(pane, "mark.comparison-equal,mark.comparison-different");
    pane
      .querySelectorAll(".aligned-group")
      .forEach((group) =>
        group.replaceWith(...group.querySelectorAll(".passage")),
      );
  }
  $("#transcript").style.marginTop = "";
  $("#comparison-transcript").style.marginTop = "";
}
function markWording(node, spans, className = null) {
  const paragraph = node.querySelector(".passage-text");
  if (!paragraph || !spans?.length) return;
  const walker = document.createTreeWalker(paragraph, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  let offset = 0,
    firstSpan = 0;
  for (const textNode of nodes) {
    const chars = Array.from(textNode.data),
      end = offset + chars.length;
    const fragment = document.createDocumentFragment();
    let cursor = 0;
    while (firstSpan < spans.length && spans[firstSpan].end <= offset)
      firstSpan++;
    for (
      let index = firstSpan;
      index < spans.length && spans[index].start < end;
      index++
    ) {
      const span = spans[index],
        start = Math.max(0, span.start - offset),
        stop = Math.min(chars.length, span.end - offset);
      if (stop <= start) continue;
      fragment.append(
        document.createTextNode(chars.slice(cursor, start).join("")),
      );
      const mark = document.createElement("mark");
      mark.className =
        className || (span.equal ? "comparison-equal" : "comparison-different");
      mark.textContent = chars.slice(start, stop).join("");
      fragment.append(mark);
      cursor = stop;
    }
    fragment.append(document.createTextNode(chars.slice(cursor).join("")));
    textNode.replaceWith(fragment);
    offset = end;
  }
}
function searchExpression(query, global = false) {
  return query
    ? new RegExp(
        query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
        global ? "giu" : "iu",
      )
    : null;
}
function highlightSearch(segments, prefix) {
  const expression = searchExpression($("#transcript-search").value, true);
  for (const segment of segments) {
    const node = document.getElementById(prefix + segment.id);
    if (!node) continue;
    clearWordingMarks(node, "mark.search-match");
    if (!expression) continue;
    const spans = Array.from(segment.text.matchAll(expression), (match) => {
      const start = Array.from(segment.text.slice(0, match.index)).length;
      return { start, end: start + Array.from(match[0]).length };
    });
    markWording(node, spans, "search-match");
  }
}
function scheduleAlignmentLayout() {
  if (alignmentFrame !== null) cancelAnimationFrame(alignmentFrame);
  alignmentFrame = requestAnimationFrame(() => {
    alignmentFrame = null;
    if (!comparisonAligned) return;
    const left = [...$("#transcript").querySelectorAll(".aligned-group")];
    const right = [
      ...$("#comparison-transcript").querySelectorAll(".aligned-group"),
    ];
    [...left, ...right].forEach((node) => (node.style.minHeight = ""));
    $("#transcript").style.marginTop = "";
    $("#comparison-transcript").style.marginTop = "";
    if (
      Math.abs(
        $("#transcript").getBoundingClientRect().left -
          $("#comparison-transcript").getBoundingClientRect().left,
      ) < 100
    )
      return;
    const gap =
      $("#transcript").getBoundingClientRect().top -
      $("#comparison-transcript").getBoundingClientRect().top;
    $("#transcript").style.marginTop = Math.max(0, -gap) + "px";
    $("#comparison-transcript").style.marginTop = Math.max(0, gap) + "px";
    const heights = left.map((node, i) =>
      Math.ceil(
        Math.max(
          node.getBoundingClientRect().height,
          right[i].getBoundingClientRect().height,
        ),
      ),
    );
    left.forEach((node, i) => {
      node.style.minHeight = heights[i] + "px";
      right[i].style.minHeight = heights[i] + "px";
    });
  });
}
async function refreshComparisonAlignment() {
  if (!state.comparison || !state.recording?.active_transcript_id) return;
  const token = ++alignmentToken;
  const key = comparisonKey();
  try {
    if (alignmentCache?.key !== key) {
      const path =
        "/recordings/" +
        state.recording.id +
        "/alignment?left_id=" +
        state.recording.active_transcript_id +
        "&right_id=" +
        state.comparison.id;
      alignmentCache = { key, promise: api(path) };
    }
    if (!alignmentCache.rows) {
      $("#comparison-info").textContent = "Aligning matching passages…";
      const cached = alignmentCache;
      cached.data = await cached.promise;
      cached.rows = cached.data.rows;
    }
    if (
      token !== alignmentToken ||
      key !== comparisonKey() ||
      alignmentCache?.key !== key
    )
      return;
    clearComparisonAlignment();
    const left = new Map(
      [...$("#transcript").querySelectorAll("[data-segment]")].map((node) => [
        node.dataset.segment,
        node,
      ]),
    );
    const right = new Map(
      [
        ...$("#comparison-transcript").querySelectorAll(
          "[data-compare-segment]",
        ),
      ].map((node) => [node.dataset.compareSegment, node]),
    );
    const leftRows = document.createDocumentFragment(),
      rightRows = document.createDocumentFragment();
    alignmentCache.rows.forEach((row, i) => {
      for (const [side, nodes, fragment] of [
        ["left", left, leftRows],
        ["right", right, rightRows],
      ]) {
        const group = document.createElement("div");
        group.className = "aligned-group";
        if (alignmentCache.data.confidence?.[i] === "partial") {
          const note = document.createElement("p");
          note.className = "alignment-confidence";
          note.textContent = "Partial wording match · review the differences";
          group.append(note);
        }
        group.dataset.alignmentRow = i;
        for (const id of row[side])
          if (nodes.has(id)) group.append(nodes.get(id));
        if (!group.children.length) {
          const gap = document.createElement("p");
          gap.className = "alignment-gap";
          gap.textContent = "No matching passage";
          group.append(gap);
        }
        fragment.append(group);
      }
      if (
        featureOn("wording_adoption") &&
        row.left.length === 1 &&
        row.right.length === 1 &&
        left.has(row.left[0]) &&
        right.has(row.right[0])
      ) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "text-button wording-adopt";
        button.textContent = "Use wording";
        button.dataset.useWording = row.right[0];
        button.dataset.wordingTarget = row.left[0];
        button.dataset.comparisonKey = key;
        button.disabled = pendingResultMutations.has(state.recording.id);
        right
          .get(row.right[0])
          .querySelector(".passage-heading")
          .append(button);
      }
    });
    $("#transcript").replaceChildren(leftRows);
    $("#comparison-transcript").replaceChildren(rightRows);
    for (const [side, nodes] of [
      ["left", left],
      ["right", right],
    ]) {
      for (const [id, node] of nodes)
        markWording(node, alignmentCache.data.wording?.[side]?.[id]);
    }
    comparisonAligned = true;
    $("#comparison-info").textContent =
      resultLabel(state.comparison) +
      " · Read only. Matching passages aligned by words and phrases; underlined wording matches, amber wording differs." +
      untimedNote(state.comparison);
    [...left.values(), ...right.values()].forEach((node) =>
      alignmentObserver.observe(node),
    );
    scheduleAlignmentLayout();
  } catch {
    if (token === alignmentToken)
      $("#comparison-info").textContent =
        "Alignment unavailable. Passages remain in their original order.";
  }
}
window.addEventListener("resize", scheduleAlignmentLayout);
document.fonts.ready.then(scheduleAlignmentLayout);
for (const [pane, other] of [
  [$("#transcript"), $("#comparison-transcript")],
  [$("#comparison-transcript"), $("#transcript")],
]) {
  pane.addEventListener(
    "scroll",
    () => {
      if (automaticTranscriptScrolls.delete(pane)) return;
      if (
        comparisonAligned &&
        Math.abs(
          pane.getBoundingClientRect().left -
            other.getBoundingClientRect().left,
        ) > 100 &&
        Math.abs(pane.scrollTop - other.scrollTop) > 1
      ) {
        const previous = other.scrollTop;
        other.scrollTop = pane.scrollTop;
        // Do not echo a mirrored scroll back and interrupt native keyboard scrolling.
        if (other.scrollTop !== previous) automaticTranscriptScrolls.add(other);
      }
    },
    { passive: true },
  );
}
$("#comparison-transcript").addEventListener("click", (event) => {
  const wording = event.target.closest("[data-use-wording]");
  if (wording) {
    event.stopPropagation();
    openWordingAdoption(wording);
    return;
  }
  if (event.target.closest(".passage-repair")) return;
  const node = event.target.closest("[data-compare-segment]");
  const segment = state.comparison?.segments.find(
    (s) => s.id === node?.dataset.compareSegment,
  );
  if (!segment) return;
  const word = event.target.closest("[data-word]");
  seekTo(word ? segment.words[Number(word.dataset.word)].start : segment.start);
});
let wordingAdoptionContext = null;
function sameWordingView(context) {
  return (
    sameRecordingView(context) &&
    state.comparison?.id === context.source &&
    comparisonKey() === context.key
  );
}
function currentWordingAdoption(context) {
  return (
    wordingAdoptionContext === context &&
    $("#wording-adoption-dialog").open &&
    sameWordingView(context)
  );
}
function wordingPassageLabel(label, passage) {
  if (!passage) return label;
  const bounds =
    passage.start == null && passage.end == null
      ? "No timestamps"
      : `${passage.start ?? "—"}–${passage.end ?? "—"} s`;
  return `${label} · Passage ${passage.ordinal} · ${bounds}`;
}
function renderWordingAdoption(context) {
  const preview = context.preview,
    busy = context.loading || context.applying;
  $("#wording-adoption-refresh").disabled =
    busy || pendingResultMutations.has(context.id);
  $("#wording-adoption-apply").disabled =
    busy ||
    context.needsRefresh ||
    !preview ||
    pendingResultMutations.has(context.id);
  $("#wording-adoption-source").textContent =
    `Comparison: ${preview?.source_label || context.sourceLabel}`;
  $("#wording-adoption-target-heading").textContent = wordingPassageLabel(
    "Current reviewed wording",
    preview?.target_passage,
  );
  $("#wording-adoption-source-heading").textContent = wordingPassageLabel(
    "Comparison wording",
    preview?.source_passage,
  );
  $("#wording-adoption-target-text").textContent =
    preview?.target_passage.text || "";
  $("#wording-adoption-source-text").textContent =
    preview?.source_passage.text || "";
}
async function loadWordingAdoption(context) {
  if (
    !currentWordingAdoption(context) ||
    context.loading ||
    context.applying ||
    pendingResultMutations.has(context.id)
  )
    return;
  context.loading = true;
  context.needsRefresh = true;
  $("#wording-adoption-error").hidden = true;
  $("#wording-adoption-status").textContent = "Loading current passages…";
  renderWordingAdoption(context);
  let requestingPreview = false;
  try {
    const current = await api(`/recordings/${context.id}`);
    if (!currentWordingAdoption(context)) return;
    if (
      current.active_transcript_id !== context.result ||
      current.revision < state.recording.revision
    )
      throw new Error(
        "The reviewed result changed in another window. Close this preview and reload the recording.",
      );
    requestingPreview = true;
    const preview = await api(
      `/recordings/${context.id}/transcripts/${context.result}/wording-preview`,
      {
        method: "POST",
        body: {
          source_result_id: context.source,
          target_passage_id: context.targetPassage,
          source_passage_id: context.sourcePassage,
          revision: current.revision,
        },
      },
    );
    if (!currentWordingAdoption(context)) return;
    if (
      preview.result_id !== context.result ||
      preview.source_result_id !== context.source ||
      preview.target_passage.id !== context.targetPassage ||
      preview.source_passage.id !== context.sourcePassage ||
      preview.revision !== current.revision ||
      !/^[a-f0-9]{64}$/.test(preview.fingerprint)
    )
      throw new Error(
        "The wording preview does not match these selected passages.",
      );
    context.preview = preview;
    context.needsRefresh = false;
    $("#wording-adoption-status").textContent =
      "Preview only. Check both passages before using the comparison wording.";
  } catch (error) {
    if (currentWordingAdoption(context)) {
      $("#wording-adoption-status").textContent =
        "Refresh the preview before using this wording.";
      $("#wording-adoption-error").textContent =
        requestingPreview &&
        error.status === 404 &&
        error.message === "Not Found"
          ? "Comparison wording previews need the updated backend. Restart CBS Transcriber to use this feature."
          : error.status === 404
            ? "A selected passage or result no longer exists. Close this preview, reload the recording and reselect the comparison."
            : error.message;
      $("#wording-adoption-error").hidden = false;
    }
  } finally {
    context.loading = false;
    if (currentWordingAdoption(context)) renderWordingAdoption(context);
  }
}
async function openWordingAdoption(button) {
  if (!featureOn("wording_adoption")) return;
  const key = comparisonKey(),
    target = button.dataset.wordingTarget,
    source = button.dataset.useWording;
  if (
    !state.comparison ||
    !comparisonAligned ||
    alignmentCache?.key !== key ||
    button.dataset.comparisonKey !== key ||
    !alignmentCache.rows.some(
      (row) =>
        row.left.length === 1 &&
        row.right.length === 1 &&
        row.left[0] === target &&
        row.right[0] === source,
    ) ||
    pendingResultMutations.has(state.recording?.id) ||
    $("#wording-adoption-dialog").open
  )
    return;
  const context = {
    ...recordingContext(),
    key,
    source: state.comparison.id,
    sourceLabel: state.comparison.label,
    targetPassage: target,
    sourcePassage: source,
    preview: null,
    needsRefresh: true,
  };
  wordingAdoptionContext = context;
  $("#wording-adoption-dialog").showModal();
  await loadWordingAdoption(context);
}
$("#wording-adoption-dialog").addEventListener("close", () => {
  pendingWorkingEditChoice = null;
  wordingAdoptionContext = null;
});
$("#wording-adoption-refresh").addEventListener("click", () => {
  if (wordingAdoptionContext) loadWordingAdoption(wordingAdoptionContext);
});
$("#wording-adoption-apply").addEventListener("click", async () => {
  const context = wordingAdoptionContext;
  if (
    !context ||
    !currentWordingAdoption(context) ||
    context.loading ||
    context.applying ||
    context.needsRefresh ||
    !context.preview ||
    pendingResultMutations.has(context.id) ||
    $("#save-status").textContent === "Saving…" ||
    !canDiscardPassage()
  )
    return;
  context.applying = true;
  pendingResultMutations.add(context.id);
  $("#wording-adoption-error").hidden = true;
  $("#wording-adoption-status").textContent = "Using comparison wording…";
  renderWordingAdoption(context);
  renderResults();
  const controls = [
    ...document.querySelectorAll(
      "#transcript textarea, #transcript button, #comparison-transcript [data-use-wording], #result-select, #compare-result, #results-manage",
    ),
  ];
  const disabled = controls.map((control) => control.disabled);
  controls.forEach((control) => {
    control.disabled = true;
  });
  try {
    const result = await api(
      `/recordings/${context.id}/transcripts/${context.result}/wording-adopt`,
      {
        method: "POST",
        body: {
          source_result_id: context.source,
          target_passage_id: context.targetPassage,
          source_passage_id: context.sourcePassage,
          revision: context.preview.revision,
          fingerprint: context.preview.fingerprint,
        },
      },
    );
    if (!sameWordingView(context)) return;
    let comparison = null;
    try {
      comparison = await api(
        `/recordings/${context.id}/transcripts/${context.source}`,
      );
    } catch {
      // The adoption is already committed; a failed comparison reload cannot undo it.
    }
    if (!sameWordingView(context)) return;
    const recording = result.recording;
    if (!acceptRecording(context, recording)) return;
    clearEdit();
    state.comparison = comparison;
    renderEditorDetails();
    renderTranscript();
    if (wordingAdoptionContext === context)
      $("#wording-adoption-dialog").close();
    toast(
      comparison
        ? "Comparison wording adopted. This passage needs new word timing."
        : "Comparison wording adopted. The comparison could not reload; select it again.",
    );
  } catch (error) {
    if (currentWordingAdoption(context)) {
      context.needsRefresh = true;
      $("#wording-adoption-status").textContent =
        "No wording change was confirmed. Refresh the preview before trying again.";
      $("#wording-adoption-error").textContent = error.message;
      $("#wording-adoption-error").hidden = false;
    }
  } finally {
    context.applying = false;
    pendingResultMutations.delete(context.id);
    if (currentWordingAdoption(context)) renderWordingAdoption(context);
    if (context.epoch === viewEpoch && context.id === state.recording?.id) {
      controls.forEach((control, index) => {
        if (control.isConnected) control.disabled = disabled[index];
      });
      renderResults();
    }
  }
});
$("#export-group").addEventListener("change", updateExport);
$("#export-results").addEventListener("change", updateExport);
$("#back-library").addEventListener("click", () => {
  location.hash = `/projects/${state.recording.project_id}`;
});
function renderRecordingRenameControl() {
  $("#recording-rename").disabled =
    !state.recording || pendingRecordingRenames.has(state.recording.id);
}
$("#recording-rename").addEventListener(
  "click",
  handle(async () => {
    const context = recordingContext();
    if (pendingRecordingRenames.has(context.id)) return;
    const name = await nameDialog("Rename recording", state.recording.name);
    if (!name || !sameRecordingView(context)) return;
    pendingRecordingRenames.add(context.id);
    renderRecordingRenameControl();
    try {
      const recording = await api(`/recordings/${context.id}`, {
        method: "PATCH",
        body: { name },
      });
      if (!acceptRecording(context, recording)) return;
      renderEditorDetails();
    } finally {
      pendingRecordingRenames.delete(context.id);
      renderRecordingRenameControl();
    }
  }),
);
$("#speakers").addEventListener(
  "click",
  handle(async (event) => {
    const button = event.target.closest("[data-speaker]");
    if (!button || !canLeave()) return;
    const speaker = button.dataset.speaker;
    const target = manualEditTarget(
      `/recordings/${state.recording.id}/speakers`,
      "PATCH",
      { speaker },
    );
    if (!(await workingEditChoice(target, true))) return;
    const context = recordingContext();
    const name = await nameDialog(
      "Rename speaker",
      speakerName(state.recording, speaker),
    );
    if (!name || !sameRecordingView(context)) {
      pendingWorkingEditChoice = null;
      return;
    }
    const revision = state.recording.revision;
    const recording = await api(`/recordings/${context.id}/speakers`, {
      method: "PATCH",
      body: { speaker, name, revision },
    });
    const updateBadges =
      !state.comparison &&
      !state.editing &&
      !state.dirty &&
      state.recording?.revision === revision &&
      recording.revision === revision + 1;
    if (!acceptRecording(context, recording)) return;
    clearEdit();
    renderEditorDetails();
    if (updateBadges) refreshSpeakerBadges(recording, revision);
    else renderTranscript();
  }),
);
$("#file-settings-open").addEventListener("click", () => {
  if (!state.recording) return;
  $("#file-settings-title").textContent =
    `File settings · ${state.recording.name}`;
  $("#file-delete-error").hidden = true;
  $("#file-delete-progress").hidden = true;
  $("#file-delete").disabled = pendingDeletions.has(
    `/recordings/${state.recording.id}`,
  );
  $("#file-settings-dialog").showModal();
});
$("#file-delete").addEventListener(
  "click",
  handle(async () => {
    const recording = state.recording;
    const context = recordingContext();
    const dialog = $("#file-settings-dialog");
    const current = () => sameRecordingView(context) && dialog.open;
    if (
      !recording ||
      !canLeave() ||
      !confirm(
        `Cancel jobs and delete "${recording.name}" and its app-managed files? Your original source file is unaffected.`,
      )
    )
      return;
    $("#file-delete").disabled = true;
    $("#file-delete-error").hidden = true;
    try {
      const deleted = await deleteManaged(
        `/recordings/${recording.id}`,
        current,
        (message) => {
          $("#file-delete-progress").textContent = message;
          $("#file-delete-progress").hidden = false;
        },
      );
      if (!deleted || !current()) return;
    } catch (error) {
      if (current()) {
        $("#file-delete-error").textContent = error.message;
        $("#file-delete-error").hidden = false;
      }
      return;
    } finally {
      if (current()) {
        $("#file-delete-progress").hidden = true;
        $("#file-delete").disabled = false;
      }
    }
    dialog.close();
    location.hash = `#/projects/${recording.project_id}`;
    await refreshProjects();
  }),
);
$("#import-transcript").addEventListener("click", () => {
  $("#file-settings-dialog").close();
  if (canLeave()) $("#transcript-file").click();
});
$("#transcript-file").addEventListener(
  "change",
  handle(async (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    const recording = state.recording;
    if (!recording || location.hash !== `#/recordings/${recording.id}`) {
      toast(
        "Wait for this recording to finish loading, then choose the transcript again.",
        true,
      );
      return;
    }
    if (
      recording.segments.length &&
      !confirm(
        "Import this as a new saved result and select it for review? Existing results are kept.",
      )
    )
      return;
    const data = new FormData();
    data.append("file", file);
    data.append("revision", recording.revision);
    data.append("replace", "true");
    const context = recordingContext();
    const imported = await api(`/recordings/${recording.id}/transcript`, {
      method: "POST",
      body: data,
    });
    if (!acceptRecording(context, imported)) return;
    clearEdit();
    renderEditorDetails();
    renderTranscript();
    toast("Transcript imported");
  }),
);

const wordPlaybackIndexes = new WeakMap();
function playbackWords(segment) {
  let indexes = wordPlaybackIndexes.get(segment);
  if (indexes) return indexes;
  indexes = new Set();
  if (
    segment.word_timing ||
    !segment.edited ||
    segment.words_current === true
  ) {
    let previousStart = -1,
      previousEnd = -1;
    for (const [index, word] of (segment.words || []).entries()) {
      if (
        Number.isFinite(word.start) &&
        Number.isFinite(word.end) &&
        word.start >= 0 &&
        word.end > word.start &&
        word.start >= previousStart &&
        word.end >= previousEnd &&
        (segment.start == null || word.start >= segment.start) &&
        (segment.end == null || word.end <= segment.end)
      ) {
        indexes.add(index);
        previousStart = word.start;
        previousEnd = word.end;
      }
    }
  }
  wordPlaybackIndexes.set(segment, indexes);
  return indexes;
}
function replayWindow(segment) {
  if (
    Number.isFinite(segment.start) &&
    Number.isFinite(segment.end) &&
    segment.end > segment.start
  )
    return { start: segment.start, end: segment.end };
  const source = state.recording.replay_windows?.[segment.id];
  if (source) return source;
  const duration = state.recording.duration;
  if (Number.isFinite(duration) && duration > 0)
    return { start: 0, end: duration, fullRecording: true };
}
const renderedWordHints = new WeakMap();
function renderPassageText(
  paragraph,
  segment,
  editable = state.mode === "word",
  marks = [],
) {
  const indexes = playbackWords(segment);
  const characters = Array.from(segment.text);
  const fragment = document.createDocumentFragment();
  // Uncertain ranges inside plain text (untimed words or gaps between timed words).
  const plain = (from, to) => {
    let cursor = from;
    for (const [start, end, , label] of marks) {
      const left = Math.max(start, cursor),
        right = Math.min(end, to);
      if (right <= left) continue;
      fragment.append(
        document.createTextNode(characters.slice(cursor, left).join("")),
      );
      const span = document.createElement("span");
      span.className = "uncertain-word";
      span.title = label ?? "";
      span.textContent = characters.slice(left, right).join("");
      fragment.append(span);
      cursor = right;
    }
    fragment.append(
      document.createTextNode(characters.slice(cursor, to).join("")),
    );
  };
  let cursor = 0;
  segment.words?.forEach((word, index) => {
    if (!indexes.has(index)) return;
    plain(cursor, word.char_start);
    const mark = marks.find(
      ([start, end]) => start < word.char_end && word.char_start < end,
    );
    const hint = editable
      ? "Click to play · Double-click to edit passage"
      : "Click to play";
    const button = document.createElement("button");
    button.type = "button";
    button.className = `word${mark ? " uncertain-word" : ""}`;
    button.dataset.word = index;
    button.title = mark ? `${mark[3]} · ${hint}` : hint;
    button.textContent = word.word;
    fragment.append(button);
    cursor = word.char_end;
  });
  plain(cursor, characters.length);
  paragraph.replaceChildren(fragment);
  renderedWordHints.set(paragraph, JSON.stringify(marks));
}
function passageEditText(segment, input) {
  // textarea.value normalizes line endings even when the researcher made no edit.
  return input.value === segment.text.replace(/\r\n?/g, "\n")
    ? segment.text
    : input.value;
}
function passageCursorOffset(text, position) {
  let offset = 0;
  for (let cursor = 0; cursor < position && offset < text.length; cursor++) {
    if (text[offset] === "\r" && text[offset + 1] === "\n") offset++;
    offset++;
  }
  return Array.from(text.slice(0, offset)).length;
}
const hintCache = new Map();
const pendingHints = new Map();
let hintVersion = 0;
function currentHints() {
  const r = state.recording;
  return r?.active_transcript_id
    ? hintCache.get(`${r.active_transcript_id}:${r.revision}`)
    : null;
}
async function loadHints() {
  const r = state.recording;
  if (!r?.active_transcript_id) return;
  const key = `${r.active_transcript_id}:${r.revision}`;
  if (hintCache.has(key)) return;
  const context = recordingContext();
  const pendingKey = JSON.stringify([
    context.epoch,
    context.id,
    key,
    context.editVersion,
  ]);
  if (pendingHints.has(pendingKey)) return pendingHints.get(pendingKey);
  const version = ++hintVersion;
  const pending = (async () => {
    const hints = await api(
      `/recordings/${r.id}/transcripts/${r.active_transcript_id}/hints`,
    );
    // A superseded editor load cannot replace newer cached hints for the same revision.
    if (version !== hintVersion) return;
    hintCache.set(key, hints);
    if (sameRecordingView(context) && !state.dirty && !state.editing)
      refreshTranscriptHints();
  })();
  pendingHints.set(pendingKey, pending);
  try {
    return await pending;
  } finally {
    if (pendingHints.get(pendingKey) === pending)
      pendingHints.delete(pendingKey);
  }
}

function refreshPassageHintFlag(passage, reasons) {
  const heading = passage.querySelector(".passage-heading");
  let flag = heading.querySelector(".review-flag");
  passage.classList.toggle("review", !!reasons);
  if (reasons) {
    if (!flag) {
      flag = document.createElement("span");
      flag.className = "review-flag";
      heading
        .querySelector(".timestamp")
        .insertAdjacentElement("afterend", flag);
    }
    flag.textContent = "Review suggested";
    flag.title = `Review suggested: ${reasons.join("; ")}. Model signals, not a measure of correctness.`;
  } else flag?.remove();
}
function refreshTranscriptHints() {
  if (!state.recording || state.dirty || state.editing) return;
  const review = uncertaintyView(),
    changed = [];
  for (const segment of state.recording.segments) {
    const passage = document.getElementById(`passage-${segment.id}`);
    if (!passage) continue;
    refreshPassageHintFlag(passage, review.flagged.get(segment.id));
    const text = passage.querySelector(":scope > .passage-text");
    const marks = review.words.get(segment.id) || [];
    if (text && renderedWordHints.get(text) !== JSON.stringify(marks)) {
      renderPassageText(text, segment, true, marks);
      changed.push(segment);
    }
  }
  if (changed.length) highlightSearch(changed, "passage-");
  if (activeWord && !activeWord.isConnected) activeWord = null;
  if (state.comparison) scheduleAlignmentLayout();
}
// Rank-based: mark at most `share` percent of all items, lowest first, and only items below
// a confidence ceiling, so a consistently confident transcript shows few or no marks.
const PASSAGE_CEILING = -0.25;
const WORD_CEILING = 0.5;
function lowest(items, share, ceiling) {
  // Never more than the chosen share, so very short transcripts may show no rank marks.
  const limit = Math.floor((items.length * share) / 100);
  return new Set(
    items
      .filter(([, value]) => Number.isFinite(value) && value < ceiling)
      .sort((a, b) => a[1] - b[1])
      .slice(0, limit)
      .map(([key]) => key),
  );
}
function reviewMarks(hints, share) {
  const flagged = new Map(),
    words = new Map();
  const passages = Object.entries(hints?.passages || {});
  const weakPassages = lowest(
    passages
      .filter(([, p]) => Number.isFinite(p.avg_logprob))
      .map(([id, p]) => [id, p.avg_logprob]),
    share,
    PASSAGE_CEILING,
  );
  const weakWords = lowest(
    passages.flatMap(([id, p]) =>
      (p.words || []).map((w, index) => [`${id}:${index}`, w[2]]),
    ),
    share,
    WORD_CEILING,
  );
  for (const [id, p] of passages) {
    const reasons = [];
    if (weakPassages.has(id))
      reasons.push(
        `lower decoding confidence than most passages here (average log-probability ${p.avg_logprob.toFixed(2)})`,
      );
    if (p.no_speech_prob > 0.6 && p.avg_logprob < -1)
      reasons.push("may be silence or invented text");
    if (p.compression_ratio > 2.4)
      reasons.push("repetitive output, a sign of invented text");
    const label =
      p.word_kind === "alignment"
        ? "Low alignment fit to the audio"
        : "Low transcription probability";
    const marks = (p.words || [])
      .filter((_, index) => weakWords.has(`${id}:${index}`))
      .map(([start, end, value]) => [
        start,
        end,
        value,
        `${label} (${value.toFixed(2)})`,
      ]);
    if (marks.length) words.set(id, marks);
    if (
      p.word_kind === "alignment" &&
      !Number.isFinite(p.avg_logprob) &&
      p.words?.length &&
      marks.length / p.words.length >= 0.5
    )
      reasons.push("many words fit the audio poorly");
    if (reasons.length) flagged.set(id, reasons);
  }
  return { flagged, words };
}
function uncertaintyView() {
  const hints = currentHints();
  const available = Object.keys(hints?.passages || {}).length > 0;
  $("#uncertainty-controls").hidden = !available;
  const show = available && $("#show-uncertainty").checked;
  const view = show
    ? reviewMarks(hints, Number($("#uncertainty-share").value))
    : { flagged: new Map(), words: new Map() };
  // A tag beside "Repair all…": the flagged passages that have an audio interval to send.
  const segments = state.recording?.segments || [];
  const flagged = segments.filter((s) => view.flagged.has(s.id));
  const ids = flagged
    .filter(retranscribable)
    .map((s) => s.id)
    .slice(0, 200);
  const tag = $("#retranscribe-flagged");
  tag.hidden = !ids.length || !featureOn("retranscribe");
  tag.dataset.ids = JSON.stringify(ids);
  tag.textContent =
    ids.length === flagged.length
      ? `Re-transcribe all ${ids.length} of ${segments.length} flagged passages`
      : `Re-transcribe ${ids.length} of ${flagged.length} flagged passages`;
  tag.title = `New wording from WhisperX for the passages marked "Review suggested", saved as a separate result to compare.${ids.length < flagged.length ? " Only flagged passages with a known audio interval of at most 60 seconds are sent, at most 200 at a time." : ""}`;
  updateTranscriptTags();
  $("#uncertainty-note").textContent = available
    ? `${view.flagged.size} passage(s) suggested for review${hints.edited_passages ? ` · ${hints.edited_passages} edited passage(s) no longer show model signals` : ""}`
    : "";
  return view;
}
for (const id of ["#show-uncertainty", "#uncertainty-share"])
  $(id).addEventListener("change", () => {
    if (!state.dirty) renderTranscript();
  });
// T71: new wording for chosen passages from WhisperX, as a separate result to compare.
function retranscribable(segment) {
  return (
    segment.start != null &&
    segment.end != null &&
    segment.end > segment.start &&
    segment.end - segment.start <= 60
  );
}
// One-click processing (ADR 0059): the saved Processing settings and Automatic place; the
// only question is whether UCloud may first prepare its runtime (409 prepare_runtime).
async function processingDefaults() {
  const settings = await api("/enhancement-settings");
  return {
    runtime_python: settings.runtime_python,
    device: settings.device,
    language: settings.language,
    allow_downloads: settings.download_models,
  };
}
async function withPreparation(path, body) {
  try {
    return await api(path, { method: "POST", body });
  } catch (error) {
    if (error.code !== "prepare_runtime" || !confirm(error.message))
      throw error;
    return await api(path, {
      method: "POST",
      body: { ...body, prepare_runtime: true },
    });
  }
}
async function retranscribeNow(ids) {
  const recording = state.recording;
  if (
    !featureOn("retranscribe") ||
    !recording?.active_transcript_id ||
    !ids.length
  )
    return;
  const context = recordingContext();
  await withPreparation(`/recordings/${recording.id}/retranscriptions`, {
    ...(await processingDefaults()),
    execution: processingPlace(),
    align: true,
    diarize: false,
    whisper_model: "large-v3-turbo",
    result_id: recording.active_transcript_id,
    revision: recording.revision,
    segment_ids: ids,
  });
  if (!sameRecordingView(context)) return;
  await pollJobs();
  toast(
    `Re-transcribing ${ids.length} passage(s) with WhisperX. The new wording is saved as a separate result to compare; this result is kept.`,
  );
}
async function enhanceWholeResult(features, question) {
  const recording = state.recording;
  if (!recording?.active_transcript_id || !confirm(question)) return;
  const context = recordingContext();
  await withPreparation(`/recordings/${recording.id}/enhancements`, {
    ...(await processingDefaults()),
    execution: processingPlace(),
    ...features,
    result_id: recording.active_transcript_id,
    revision: recording.revision,
  });
  if (!sameRecordingView(context)) return;
  await pollJobs();
  toast("Queued. A new result is saved when it finishes; this result is kept.");
}
function needsSpeakers(recording) {
  return (
    !!recording?.active_transcript_id &&
    recording.segments.length > 0 &&
    recording.segments.every((s) => !s.speaker || s.speaker === "UNKNOWN")
  );
}
$("#speaker-tag").addEventListener(
  "click",
  handle(() =>
    enhanceWholeResult(
      { align: true, diarize: true },
      `Detect speakers for the whole recording? ${processingWhere()} A new result with speaker labels is saved; this result is kept.`,
    ),
  ),
);
$("#retranscribe-flagged").addEventListener(
  "click",
  handle(async (event) => {
    const ids = JSON.parse(event.currentTarget.dataset.ids || "[]");
    if (!ids.length)
      return toast(
        "No flagged passage has a known audio interval to re-transcribe.",
      );
    if (
      confirm(
        `Re-transcribe ${ids.length} flagged passage(s) with WhisperX large-v3-turbo? ${processingWhere(true)} The new wording is saved as a separate result to compare.`,
      )
    )
      await retranscribeNow(ids);
  }),
);
// The tag row shows only when one of its tags does.
function updateTranscriptTags() {
  $("#speaker-tag").hidden = !needsSpeakers(state.recording);
  $("#transcript-tags").hidden =
    $("#timing-warning").hidden &&
    $("#retranscribe-flagged").hidden &&
    $("#speaker-tag").hidden;
}
function expectsWordTiming(result) {
  const source =
    result.source ||
    result.transcripts?.find((t) => t.id === result.active_transcript_id)
      ?.source;
  return (
    ["words", "words_speakers"].includes(source?.capability) ||
    source?.options?.align === true ||
    result.segments.some(
      (s) => s.words?.length || s.original_words?.length || s.word_timing,
    )
  );
}
function timingGap(segment, expectedTiming) {
  return !segment.word_timing && expectedTiming;
}
function passageTimingCoverage(segment) {
  const characters = Array.from(segment.text),
    covered = new Uint8Array(characters.length),
    indexes = playbackWords(segment);
  let previousChar = 0;
  for (const index of indexes) {
    const word = segment.words[index],
      left = word.char_start,
      right = word.char_end;
    if (
      !Number.isInteger(left) ||
      !Number.isInteger(right) ||
      left < previousChar ||
      left < 0 ||
      right <= left ||
      right > characters.length ||
      characters.slice(left, right).join("") !== word.word
    )
      continue;
    covered.fill(1, left, right);
    previousChar = right;
  }
  // Match research-report coverage: a lexical whitespace token counts only when
  // all its letters/numbers/underscores have current, usable word intervals.
  let total = 0,
    timed = 0,
    lexical = false,
    located = true;
  for (let index = 0; index <= characters.length; index++) {
    const character = characters[index];
    if (character == null || /\s/u.test(character)) {
      if (lexical) {
        total++;
        if (located) timed++;
      }
      lexical = false;
      located = true;
    } else if (/[\p{L}\p{N}_]/u.test(character)) {
      lexical = true;
      if (!covered[index]) located = false;
    }
  }
  return { total, timed };
}
function wordTimingCoverage(result) {
  const coverage = { total: 0, timed: 0, complete: 0, partial: 0, untimed: 0 };
  for (const segment of result.segments) {
    const passage = passageTimingCoverage(segment);
    coverage.total += passage.total;
    coverage.timed += passage.timed;
    if (passage.total)
      coverage[
        segment.word_timing && passage.timed === passage.total
          ? "complete"
          : passage.timed
            ? "partial"
            : "untimed"
      ]++;
  }
  return coverage;
}
// Where a passage's word timing may be found (ADR 0056/0058): its own interval or, without
// one, the gap between the neighbouring known times, shared with adjacent untimed passages.
// The gap only limits the search; it is never stored as timing. Mirrors timing_units().
function unboundedPassage(segment) {
  return segment.start == null || segment.end == null;
}
function timingWindow(segments, index) {
  const segment = segments[index];
  if (!unboundedPassage(segment))
    return { start: segment.start, end: segment.end, members: [segment] };
  let first = index,
    last = index;
  while (first > 0 && unboundedPassage(segments[first - 1])) first--;
  while (last + 1 < segments.length && unboundedPassage(segments[last + 1]))
    last++;
  let start = 0,
    end = state.recording?.duration;
  for (let j = first - 1; j >= 0; j--)
    if (segments[j].end != null) {
      start = segments[j].end;
      break;
    }
  for (let j = last + 1; j < segments.length; j++)
    if (segments[j].start != null) {
      end = segments[j].start;
      break;
    }
  return { start, end, members: segments.slice(first, last + 1) };
}
function clampedWindow(window) {
  const duration = state.recording?.duration;
  if (window.start == null || window.end == null || duration == null)
    return window;
  return {
    ...window,
    start: Math.max(window.start, 0),
    end: Math.min(window.end, duration),
  };
}
function eligiblePassage(segment, segments = state.recording.segments) {
  const window = clampedWindow(
    timingWindow(segments, segments.indexOf(segment)),
  );
  const text = window.members.map((s) => s.text).join(" ");
  return (
    window.start != null &&
    window.end != null &&
    window.end > window.start &&
    window.end - window.start <= 60 &&
    Array.from(text).length <= 1200 &&
    text.trim().split(/\s+/u).length <= 120
  );
}
// Missing word timing is a compact "Add word timing" tag in the passage heading, like
// "Review suggested"; the reason is its tooltip. Other timing states keep a quiet note.
function timingReason(segment) {
  const currentWords = playbackWords(segment).size;
  return segment.edited &&
    !segment.word_timing &&
    segment.words_current !== true
    ? realigning(segment)
      ? "Edited passage · realigning word timing on this computer"
      : "Edited passage · free-text editing cleared its word timing"
    : segment.start == null
      ? "No timestamps supplied"
      : currentWords
        ? "Word timing incomplete · highlighting available for timed words"
        : "Word timing missing or incomplete · passage highlighting";
}
// What "Add word timing" will do for this passage; every untimed passage gets the same tag.
function timingScope(segment) {
  if (!eligiblePassage(segment))
    return "Too long to time on its own (over 60 seconds or 120 words, or no timed neighbours within 60 seconds): opens Add word timing / speakers for the whole result.";
  return unboundedPassage(segment)
    ? "This passage has no start/end of its own: it is searched only between the neighbouring timed passages, together with adjacent untimed passages. That search window is never stored as timing; uncertain words stay untimed. You can start other passages meanwhile."
    : "Aligns only this passage on this computer and adds the timing to this result. You can start other passages meanwhile.";
}
function timingTag(segment, expectedTiming) {
  if (segment.word_timing || !timingGap(segment, expectedTiming)) return "";
  // The comparison is read only: its count is in the comparison note, with no actions.
  if (state.comparison?.segments.includes(segment)) return "";
  return `<span class="passage-repair timing-tag" data-repair-result="${h(state.recording.active_transcript_id)}" data-repair-segment="${h(segment.id)}" data-reason="${h(timingReason(segment))}" data-scope="${h(timingScope(segment))}" ${eligiblePassage(segment) ? "" : "data-whole-result"}></span>`;
}
function timingNote(segment, expectedTiming) {
  if (segment.word_timing || timingGap(segment, expectedTiming)) return "";
  const text = segment.edited
    ? "Edited passage · word timing is unavailable after free-text editing"
    : segment.start == null
      ? "No timestamps supplied"
      : "Passage timing";
  return `<small class="segment-note">${text}</small>`;
}
// Passage word timing (ADR 0056): each request is its own small local job whose timing is
// added to this result in place. A tag shows only its own passage's job, so other passages
// stay available while one is aligning.
function timingJob(resultId, id) {
  const matching = state.jobs.filter(
    (j) =>
      j.realign &&
      j.recording_id === state.recording.id &&
      j.source_result_id === resultId &&
      (j.segment_id === id || j.segment_ids?.includes(id)),
  );
  return (
    matching.find(
      (job) => ["queued", "running"].includes(job.state) || job.realign_ready,
    ) || matching[0]
  );
}
function timingProgress(job, id) {
  if (job.state === "queued") return "Queued";
  if (job.state === "running") {
    if (!job.segment_ids) return "Aligning";
    const current = Math.floor(job.progress * job.segment_ids.length);
    const position = job.segment_ids.indexOf(id);
    return position < current
      ? "Aligned"
      : position === current
        ? "Aligning"
        : "Queued";
  }
  return job.realign_ready ? "Adding" : null;
}
function refreshPassageRepairs() {
  document
    .querySelectorAll(".passage-repair, .bulk-passage-repair")
    .forEach((host) => {
      const resultId = host.dataset.repairResult;
      if (resultId !== state.recording?.active_transcript_id) return;
      const ids = host.dataset.repairIds
        ? JSON.parse(host.dataset.repairIds)
        : [host.dataset.repairSegment];
      const reason = host.dataset.reason ? `${host.dataset.reason}. ` : "";
      let label, title, disabled;
      if (host.dataset.repairIds) {
        const jobs = ids.map((id) => timingJob(resultId, id)).filter(Boolean);
        const active = jobs.filter(
          (j) => ["queued", "running"].includes(j.state) || j.realign_ready,
        );
        const waiting = new Set(
          active.flatMap((j) => j.segment_ids || [j.segment_id]),
        );
        const left = ids.filter((id) => !waiting.has(id));
        const whole = host.dataset.wholeResult != null;
        disabled = !whole && !left.length;
        label =
          whole || left.length
            ? host.dataset.label
            : `Adding word timing to ${ids.length} passage(s)`;
        title = whole
          ? `${host.dataset.note} Opens Add word timing / speakers for the whole result.`
          : featureOn("ucloud_enhancement")
            ? `${host.dataset.note} Small selections use local WhisperX; larger selections can use an approved, prepared UCloud connection when original-v1 source proof is ready. Timing is added to this result as one version in History.`
            : `${host.dataset.note} Local WhisperX times the selection on this computer. Timing is added to this result as one version in History.`;
        host.dataset.runIds = JSON.stringify(left);
      } else {
        const job = timingJob(resultId, ids[0]);
        const progress = job && timingProgress(job, ids[0]);
        const retry = ["failed", "interrupted", "cancelled"].includes(
          job?.state,
        );
        disabled = !!progress;
        label =
          progress || (retry ? "Try word timing again" : "Add word timing");
        title =
          reason +
          (progress === "Adding"
            ? "Timing is ready; it is added when no passage is being edited."
            : job?.state === "completed" && !job.realign_ready && job.message
              ? `${job.message} `
              : "") +
          (progress ? "" : host.dataset.scope);
      }
      const status = `${label}:${disabled}:${title}`;
      if (host.dataset.status === status) return;
      host.dataset.status = status;
      host.innerHTML = `<button type="button" class="repair-button" data-run-repair ${disabled ? "disabled" : ""} title="${h(title)}">${h(label)}</button>`;
    });
}
for (const pane of [$("#editor-layout")]) {
  pane.addEventListener(
    "click",
    handle(async (event) => {
      const host = event.target.closest(
        ".passage-repair, .bulk-passage-repair",
      );
      const button = event.target.closest("button[data-run-repair]");
      if (!host || !button) return;
      if (host.dataset.wholeResult != null) {
        // Too long to time alone: word timing for the whole recording, as a new result.
        await enhanceWholeResult(
          { align: true, diarize: false },
          `These passages are too long to time on their own. Add word timing for the whole recording? ${processingWhere()} A new result is saved; this result is kept.`,
        );
        return;
      }
      const ids = host.dataset.repairIds
        ? JSON.parse(host.dataset.runIds || "[]")
        : [host.dataset.repairSegment];
      if (!ids.length) return;
      // An open draft stays open: the timing waits until no passage is being edited.
      const context = recordingContext();
      button.disabled = true;
      try {
        await api(`/recordings/${context.id}/passage-timing`, {
          method: "POST",
          body: {
            result_id: host.dataset.repairResult,
            revision: state.recording.revision,
            segment_ids: ids,
          },
        });
        if (!sameRecordingView(context)) return;
        await pollJobs();
        toast(
          ids.length === 1 && !host.dataset.repairIds
            ? "Adding word timing to this passage. You can start other passages meanwhile."
            : `Adding word timing to ${ids.length} passage(s). It appears as one version in History.`,
        );
      } finally {
        delete host.dataset.status;
        if (button.isConnected) refreshPassageRepairs();
      }
    }),
  );
}
// The read-only comparison names its count of untimed passages in its note, without actions.
function untimedNote(result) {
  const untimed = expectsWordTiming(result)
    ? result.segments.filter((s) => !s.word_timing).length
    : 0;
  return untimed
    ? ` ${untimed} of ${result.segments.length} passages without word timing.`
    : "";
}
// Keep word availability separate from all-or-nothing passage completeness.
function renderTimingWarning(result, selector) {
  const expected = expectsWordTiming(result),
    coverage = expected ? wordTimingCoverage(result) : null,
    missing = expected
      ? result.segments.filter(
          (s) => !s.word_timing && passageTimingCoverage(s).total,
        )
      : [];
  const target = $(selector);
  // The read-only comparison names its count in its note instead (renderComparison).
  target.hidden = !coverage?.total || selector !== "#timing-warning";
  if (selector === "#timing-warning") updateTranscriptTags();
  if (target.hidden) {
    target.innerHTML = "";
    return;
  }
  const percentage = Math.round((100 * coverage.timed) / coverage.total),
    summary = `Word timing: ${coverage.timed}/${coverage.total} words (${percentage}%) · Passages: ${coverage.complete} complete, ${coverage.partial} partial, ${coverage.untimed} untimed`,
    explanation =
      "Available word intervals, not measured timing accuracy. Partial passages still highlight their timed words; free-text edits invalidate old word maps.",
    summaryMarkup = `<small id="timing-coverage" class="timing-coverage" title="${h(explanation)}">${h(summary)}</small>`;
  if (!missing.length) {
    target.innerHTML = summaryMarkup;
    return;
  }
  const ids = missing
    .filter((s) => eligiblePassage(s, result.segments))
    .map((s) => s.id)
    .slice(0, 1000);
  const others = missing.length - ids.length;
  const passages = `incomplete passage${missing.length === 1 ? "" : "s"}`,
    note = `${missing.length} ${passages}; available timed words still highlight.${others ? ` ${others} cannot be timed in this request; use Add word timing / speakers for those.` : ""}`;
  const label =
    ids.length === missing.length
      ? `Repair all ${missing.length} ${passages}`
      : ids.length
        ? `Repair ${ids.length} of ${missing.length} ${passages}`
        : `Timing options for ${missing.length} ${passages}`;
  target.innerHTML = `${summaryMarkup}<span class="bulk-passage-repair timing-tag" data-repair-result="${h(state.recording.active_transcript_id)}" data-repair-ids="${h(JSON.stringify(ids))}" data-label="${h(label)}" data-note="${h(note)}" ${ids.length ? "" : "data-whole-result"}></span>`;
}
function refreshSpeakerBadges(recording, previousRevision) {
  const speakers = [
    ...new Set(recording.segments.map((segment) => segment.speaker)),
  ];
  const segments = new Map(
    recording.segments.map((segment) => [segment.id, segment]),
  );
  for (const badge of $("#transcript").querySelectorAll(".speaker-badge")) {
    const segment = segments.get(
      badge.closest("[data-segment]")?.dataset.segment,
    );
    if (!segment) continue;
    const name = speakerName(recording, segment.speaker);
    badge.textContent = name;
    badge.title = name;
    badge.classList.remove("s0", "s1", "s2", "s3");
    badge.classList.add(`s${speakers.indexOf(segment.speaker) % 4}`);
  }
  // Refresh hints for the new revision, repainting only changed decorations.
  loadHints().catch(() => {});
  refreshMergeUndoOptions();
}
// Eligibility is proven by the server from version history. Updating only the
// action buttons avoids replacing a passage draft when the check finishes.
let mergeUndoOptions = null;
function refreshMergeUndoOptions() {
  const recording = state.recording;
  if (!recording?.active_transcript_id) {
    $("#merge-undo-history-limit").hidden = true;
    $("#merge-undo-check-error").hidden = true;
    if (typeof renderDeletedPassages === "function")
      renderDeletedPassages(null);
    return;
  }
  const epoch = viewEpoch;
  const key = `${epoch}:${recording.id}:${recording.active_transcript_id}:${recording.revision}`;
  if (mergeUndoOptions?.key !== key) {
    $("#merge-undo-history-limit").hidden = true;
    $("#merge-undo-check-error").hidden = true;
    $("#transcript")
      .querySelectorAll(
        '[data-action="undo-merge"], [data-action="undo-split"]',
      )
      .forEach((button) => button.remove());
    const entry = { key, ids: null, splitIds: null, response: null };
    mergeUndoOptions = entry;
    api(
      `/recordings/${encodeURIComponent(recording.id)}/merge-undo-options?result_id=${encodeURIComponent(recording.active_transcript_id)}`,
      { background: true },
    )
      .then((result) => {
        if (
          mergeUndoOptions !== entry ||
          viewEpoch !== epoch ||
          state.recording?.id !== recording.id ||
          state.recording?.active_transcript_id !==
            recording.active_transcript_id ||
          state.recording?.revision !== recording.revision
        )
          return;
        if (
          result.result_id !== recording.active_transcript_id ||
          result.revision !== recording.revision ||
          !Array.isArray(result.segment_ids) ||
          !Array.isArray(result.split_segment_ids ?? []) ||
          !Array.isArray(result.deleted_segments ?? [])
        )
          throw new Error(
            "Passage undo eligibility did not match this result.",
          );
        entry.ids = new Set(result.segment_ids);
        entry.splitIds = new Set(result.split_segment_ids ?? []);
        entry.response = result;
        $("#merge-undo-history-limit").hidden = result.history_limited !== true;
        refreshMergeUndoOptions();
      })
      .catch(() => {
        if (
          mergeUndoOptions === entry &&
          viewEpoch === epoch &&
          state.recording?.id === recording.id &&
          state.recording?.active_transcript_id ===
            recording.active_transcript_id &&
          state.recording?.revision === recording.revision
        )
          $("#merge-undo-check-error").hidden = false;
      });
  }
  if (typeof renderDeletedPassages === "function")
    renderDeletedPassages(mergeUndoOptions.response);
  if (!mergeUndoOptions.ids || !mergeUndoOptions.splitIds || structureLocked())
    return;
  for (const passage of $("#transcript").querySelectorAll("[data-segment]")) {
    const actions = passage.querySelector(".passage-actions");
    if (!actions) continue;
    for (const [action, ids, label, title] of [
      [
        "undo-merge",
        mergeUndoOptions.ids,
        "↶ Undo merge",
        "Restore the two passages as they were before this merge",
      ],
      [
        "undo-split",
        mergeUndoOptions.splitIds,
        "↶ Undo split",
        "Restore the passage as it was before this split",
      ],
    ]) {
      if (
        !ids.has(passage.dataset.segment) ||
        actions.querySelector(`[data-action="${action}"]`)
      )
        continue;
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.action = action;
      button.textContent = label;
      button.title = title;
      actions.appendChild(button);
    }
  }
}
$("#merge-undo-check-retry").addEventListener("click", () => {
  mergeUndoOptions = null;
  refreshMergeUndoOptions();
});
function editableTimedWords(segment) {
  if (
    (!segment.word_timing &&
      (!Number.isFinite(segment.start) ||
        !Number.isFinite(segment.end) ||
        segment.end <= segment.start)) ||
    typeof segment.text !== "string" ||
    (!segment.word_timing &&
      segment.edited &&
      segment.words_current !== true) ||
    !Array.isArray(segment.words) ||
    !segment.words.length
  )
    return false;
  const characters = Array.from(segment.text);
  let previous = 0;
  for (const word of segment.words) {
    if (!word || typeof word !== "object") return false;
    const { char_start: start, char_end: end } = word;
    if (
      !Number.isInteger(start) ||
      !Number.isInteger(end) ||
      start < previous ||
      end <= start ||
      end > characters.length ||
      characters.slice(start, end).join("") !== word.word
    )
      return false;
    previous = end;
  }
  return true;
}
function passageEditorMarkup(segment) {
  const replay = replayWindow(segment);
  const label = replay?.fullRecording
    ? "Play full recording"
    : segment.start == null && replay
      ? "Replay source passage"
      : "Replay passage";
  const help = replay?.fullRecording
    ? "This passage has no start/end timestamps; plays the full recording."
    : segment.start == null && replay
      ? "Plays the original source passage; this fragment has no word timestamps."
      : "Replay this passage.";
  const canEditWords = editableTimedWords(segment);
  const canEditTimes =
    Number.isFinite(segment.start) &&
    Number.isFinite(segment.end) &&
    segment.end > segment.start;
  const first = canEditWords ? segment.words[0] : null;
  const value = (number) => (Number.isFinite(number) ? String(number) : "");
  const wordEditor = canEditWords
    ? `<div data-editor-pane="timing" hidden>
         <p class="split-help">${canEditTimes ? "Edit one mapped word and its start and end in seconds. Missing times stay blank until entered." : "You can correct a word, but its times cannot be changed without passage bounds."}</p>
         <div class="passage-word-fields">
           <div class="timing-word-choice"><label>Word<select data-timing-word data-selected="0" aria-label="Word to edit"></select></label><button type="button" class="button" data-action="edit-timed-word" aria-expanded="false">Edit word</button><div data-word-correction hidden><label>Corrected word<input data-timing-text aria-label="Word text" maxlength="200" spellcheck="false" value="${h(first.word)}"></label></div></div>
           <label>Start (seconds)<input data-timing-start aria-label="Word start in seconds" type="number" min="0" step="any" value="${h(value(first.start))}" ${canEditTimes ? "" : "disabled"}></label>
           <label>End (seconds)<input data-timing-end aria-label="Word end in seconds" type="number" min="0" step="any" value="${h(value(first.end))}" ${canEditTimes ? "" : "disabled"}></label>
         </div>
         <p class="split-help" data-timing-draft-note hidden>Save or cancel the text draft before saving word timing.</p>
         <div class="edit-actions"><button class="button" data-action="previous-timed-word" disabled>Previous word</button><button class="button" data-action="next-timed-word" ${segment.words.length < 2 ? "disabled" : ""}>Next word</button><button class="button" data-action="reset-timed-word">Reset word</button><button class="button" data-action="cancel-edit">Cancel</button><button class="button primary" data-action="save-timed-word">Save word</button></div>
       </div>`
    : '<p class="split-help">Word timing editing is unavailable for this passage. Add word timing or use text editing.</p>';
  return `<div class="passage-editor">
    <div class="passage-editor-toolbar">
      <label>Edit <select data-editor-mode aria-label="Editing mode for this passage"><option value="text">Text</option><option value="timing" ${canEditWords ? "" : "disabled"}>Word and timing</option></select></label>
      <button type="button" class="button passage-editor-replay" data-action="editor-replay" data-replay-label="${h(label)}" title="${h(help)}" ${replay && state.recording.status === "ready" ? "" : "disabled"}>▷ ${h(label)}</button>
    </div>
    <div data-editor-pane="text">
      <textarea aria-label="Edit passage" maxlength="200000" spellcheck="false"></textarea>
      <p class="split-help">Place the cursor between words to split. Save text changes first. If you know the split's audio position, pause playback there; unavailable timing is left unknown.</p>
      <label class="passage-realign" data-feature="editor_realign" ${featureOn("editor_realign") ? "" : "hidden"}><input type="checkbox" data-editor-realign ${featureOn("editor_realign") && (passageRealignChoice ?? $("#realign-edits").checked) ? "checked" : ""}>Realign this passage after saving on this computer</label>
      <p class="split-help" data-text-draft-note hidden>Save or reset the word changes before saving passage text.</p>
      <div class="edit-actions"><button class="button" data-action="split" disabled>Split at cursor</button><button class="button" data-action="cancel-edit">Cancel</button><button class="button primary" data-action="save-edit" disabled>Save passage</button></div>
    </div>
    ${wordEditor}
    <div class="editor-playback-preview" hidden>
      <p class="field-note" data-editor-playback-label>Saved text for playback</p>
      <div class="editor-playback-scroll" tabindex="0" role="region" aria-label="Saved text for passage playback"><p class="editor-playback-text"></p></div>
      <p class="field-note" data-editor-playback-draft hidden>Playback follows saved text and timing. Unsaved edits are not used for word highlighting.</p>
    </div>
  </div>`;
}
function renderEditorPlayback(segment, editor) {
  renderPassageText(
    editor.querySelector(".editor-playback-text"),
    segment,
    false,
  );
  editor.querySelector("[data-editor-playback-label]").textContent =
    playbackWords(segment).size
      ? "Saved text for playback"
      : "Saved text for playback · word highlighting unavailable";
}
function followEditorPlaybackWord(word) {
  const scroll = word?.closest(".editor-playback-scroll");
  if (
    !scroll ||
    scroll.closest(".editor-playback-preview")?.hidden ||
    !$("#follow-playback").checked
  )
    return;
  const box = scroll.getBoundingClientRect(),
    target = word.getBoundingClientRect();
  if (target.top < box.top + 4) scroll.scrollTop += target.top - box.top - 4;
  else if (target.bottom > box.bottom - 4)
    scroll.scrollTop += target.bottom - box.bottom + 4;
}
function timedWordDraftChanged(segment, editor) {
  const select = editor.querySelector("[data-timing-word]");
  if (!select) return false;
  const word = segment.words[Number(select.dataset.selected)];
  if (!word) return false;
  const value = (number) => (Number.isFinite(number) ? String(number) : "");
  return (
    editor.querySelector("[data-timing-text]").value !== word.word ||
    editor.querySelector("[data-timing-start]").value !== value(word.start) ||
    editor.querySelector("[data-timing-end]").value !== value(word.end)
  );
}
function populateTimedWordChoices(segment, editor) {
  const select = editor.querySelector("[data-timing-word]");
  if (!select || select.options.length === segment.words.length) return;
  select.replaceChildren(
    ...segment.words.map(
      (word, index) =>
        new Option(`${index + 1} – ${word.word}`, String(index + 1)),
    ),
  );
  select.value = String(Number(select.dataset.selected) + 1);
}
function showWordCorrection(editor, visible) {
  editor.querySelector("[data-word-correction]").hidden = !visible;
  const button = editor.querySelector('[data-action="edit-timed-word"]');
  button.textContent = visible ? "Hide word edit" : "Edit word";
  button.setAttribute("aria-expanded", String(visible));
}
function resetTimedWord(segment, editor, index) {
  const word = segment.words[index];
  if (!word) return;
  const select = editor.querySelector("[data-timing-word]");
  select.dataset.selected = String(index);
  select.value = String(index + 1);
  editor.querySelector("[data-timing-text]").value = word.word;
  editor.querySelector("[data-timing-start]").value = Number.isFinite(
    word.start,
  )
    ? String(word.start)
    : "";
  editor.querySelector("[data-timing-end]").value = Number.isFinite(word.end)
    ? String(word.end)
    : "";
  editor.querySelector('[data-action="previous-timed-word"]').disabled =
    index === 0;
  editor.querySelector('[data-action="next-timed-word"]').disabled =
    index === segment.words.length - 1;
}
function changeTimedWord(segment, editor, index) {
  const input = editor.querySelector("[data-timing-word]");
  const previous = Number(input.dataset.selected);
  if (!Number.isInteger(index) || index < 0 || index >= segment.words.length) {
    input.value = String(previous + 1);
    toast("Choose a valid word number.", true);
    return;
  }
  if (
    index !== previous &&
    timedWordDraftChanged(segment, editor) &&
    !confirm("Discard the unsaved changes to this word?")
  ) {
    input.value = String(previous + 1);
    return;
  }
  resetTimedWord(segment, editor, index);
  refreshPassageDraft(segment, editor);
}
function showTimedWordEditor(
  segment,
  editor,
  index,
  focus = false,
  selection = null,
) {
  const mode = editor?.querySelector("[data-editor-mode]");
  const timing = editor?.querySelector('[data-editor-pane="timing"]');
  if (!mode || !timing || !segment.words[index]) return false;
  mode.value = "timing";
  editor.querySelector('[data-editor-pane="text"]').hidden = true;
  timing.hidden = false;
  editor.querySelector(".editor-playback-preview").hidden = false;
  populateTimedWordChoices(segment, editor);
  resetTimedWord(segment, editor, index);
  if (focus) {
    showWordCorrection(editor, true);
    const input = editor.querySelector("[data-timing-text]");
    input.focus();
    if (selection)
      input.setSelectionRange(
        Math.min(selection[0], input.value.length),
        Math.min(selection[1], input.value.length),
      );
    else input.select();
  }
  return true;
}
function refreshPassageDraft(segment, editor) {
  const textarea = editor.querySelector("textarea");
  const textDirty = passageEditText(segment, textarea) !== segment.text;
  const wordDirty = timedWordDraftChanged(segment, editor);
  state.dirty = textDirty || wordDirty;
  editVersion++;
  saved(state.dirty ? "Unsaved changes" : undefined);
  refreshPassageSplit(editor);
  editor.querySelector('[data-action="save-edit"]').disabled =
    !textDirty || wordDirty || passageTextSaving;
  const textNote = editor.querySelector("[data-text-draft-note]");
  if (textNote) textNote.hidden = !wordDirty;
  const wordSave = editor.querySelector('[data-action="save-timed-word"]');
  if (wordSave) wordSave.disabled = textDirty;
  const wordNote = editor.querySelector("[data-timing-draft-note]");
  if (wordNote) wordNote.hidden = !textDirty;
  editor.querySelector("[data-editor-playback-draft]").hidden = !(
    textDirty || wordDirty
  );
}
function passageWordBoundary(text, cursor) {
  return (
    cursor > 0 &&
    cursor < text.length &&
    !!text.slice(0, cursor).trim() &&
    !!text.slice(cursor).trim() &&
    (/\s/u.test(text[cursor - 1]) || /\s/u.test(text[cursor]))
  );
}
function refreshPassageSplit(editor) {
  const input = editor.querySelector("textarea");
  const button = editor.querySelector('[data-action="split"]');
  const boundary = passageWordBoundary(input.value, input.selectionStart);
  button.title = boundary
    ? "Split this passage at the text cursor."
    : "Place the cursor between words, next to a space or line break.";
  button.disabled =
    state.dirty ||
    input.disabled ||
    structureLocked() ||
    timedWordSaving ||
    passageTextSaving ||
    input.selectionStart !== input.selectionEnd ||
    !boundary;
}
function closePassageEditorInPlace(segment) {
  if (state.comparison || structureLocked()) return false;
  const passage = document.getElementById(`passage-${segment.id}`);
  const editor = passage?.querySelector(":scope > .passage-editor");
  if (!editor) return false;
  const review = uncertaintyView();
  const text = document.createElement("p");
  text.className = "passage-text";
  renderPassageText(text, segment, true, review.words.get(segment.id) || []);
  editor.replaceWith(text);
  refreshPassageHintFlag(passage, review.flagged.get(segment.id));
  highlightSearch([segment], "passage-");
  if (activeWord && !activeWord.isConnected) activeWord = null;
  if (typeof refreshManualReviewControls === "function")
    refreshManualReviewControls(segment.id);
  updatePlaybackButtons();
  refreshTranscriptHints();
  loadHints().catch(() => {});
  return true;
}
function openPassageEditorInPlace(segment) {
  if (state.comparison || structureLocked()) return false;
  const passage = document.getElementById(`passage-${segment.id}`);
  const text = passage?.querySelector(":scope > .passage-text");
  if (!text) return false;
  text.insertAdjacentHTML("beforebegin", passageEditorMarkup(segment));
  text.remove();
  const input = passage.querySelector("textarea");
  input.value = segment.text;
  renderEditorPlayback(segment, passage.querySelector(".passage-editor"));
  if (passage.querySelector("[data-timing-word]"))
    resetTimedWord(segment, passage, 0);
  input.focus();
  updatePlaybackButtons();
  loadHints().catch(() => {});
  return true;
}
function renderTranscript() {
  const recording = state.recording;
  if (!recording) return;
  const expectedTiming = expectsWordTiming(recording);
  renderTimingWarning(recording, "#timing-warning");
  const filter = searchExpression($("#transcript-search").value);
  $("#edit-mode").value = state.mode;
  // The mode's help is its tooltip rather than a line above the passages.
  $("#edit-mode").title =
    state.mode === "word"
      ? "Click a word to play from it. Double-click to edit the passage text, or choose Correct to replace one word within its original interval."
      : "Edit passage text. Without realignment, keep existing times when the change can be matched safely; added words stay untimed.";
  const speakers = [...new Set(recording.segments.map((s) => s.speaker))];
  const segmentIndexes = new Map(),
    indexesById = new Map();
  recording.segments.forEach((segment, index) => {
    if (!segmentIndexes.has(segment)) segmentIndexes.set(segment, index);
    if (!indexesById.has(segment.id)) indexesById.set(segment.id, index);
  });
  const review = uncertaintyView();
  loadHints().catch(() => {});
  $("#transcript").innerHTML = recording.segments.length
    ? recording.segments
        .filter((s) => state.comparison || !filter || filter.test(s.text))
        .map((segment) => {
          const segmentIndex = segmentIndexes.get(segment);
          const editing = state.editing === segment.id;
          const replay = replayWindow(segment);
          const replayLabel = replay?.fullRecording
            ? "Replay"
            : segment.start == null && replay
              ? "Replay source passage"
              : "Replay";
          const replayHelp = !replay
            ? "The recording is not ready for playback."
            : replay.fullRecording
              ? "This passage has no start/end timestamps. Plays the full recording; no timing is inferred for this passage."
              : segment.start == null
                ? "Plays the original source passage; this fragment has no word timestamps"
                : "Replay this passage";
          const reasons = review.flagged.get(segment.id);
          return `<article class="${["passage", timingGap(segment, expectedTiming) && "timing-gap", reasons && "review"].filter(Boolean).join(" ")}" id="passage-${segment.id}" data-segment="${segment.id}"><div class="passage-heading"><span class="speaker-badge s${speakers.indexOf(segment.speaker) % 4}" title="${h(speakerName(recording, segment.speaker))}">${h(speakerName(recording, segment.speaker))}</span><button class="timestamp" data-action="seek" ${segment.start == null ? "disabled" : ""}>${time(segment.start)}</button>${reasons ? `<span class="review-flag" title="${h(`Review suggested: ${reasons.join("; ")}. Model signals, not a measure of correctness.`)}">Review suggested</span>` : ""}${timingTag(segment, expectedTiming)}<div class="passage-actions"><button data-action="replay" aria-label="${replayLabel} ${segmentIndex + 1}" ${replay ? "" : "disabled"} title="${replayHelp}">▷ ${replayLabel}</button><button data-action="edit" ${state.mode === "word" && !segment.word_timing ? 'disabled title="Use Free text or restore original timing"' : ""}>${state.mode === "word" ? "Correct" : "Edit"}</button>${state.mode === "free" ? `<button data-action="merge-up" ${segmentIndex === 0 ? "disabled" : ""}>Merge up</button><button data-action="merge-down" ${segmentIndex === recording.segments.length - 1 ? "disabled" : ""}>Merge down</button>${featureOn("retranscribe") ? `<button data-action="retranscribe" ${retranscribable(segment) ? "" : 'disabled title="Needs a known audio interval of at most 60 seconds"'}>Re-transcribe</button>` : ""}<button class="danger" data-action="delete" aria-label="Delete passage ${segmentIndex + 1}" title="Delete this passage; earlier versions remain in History">Delete</button>` : ""}${segment.edited ? '<button data-action="restore">↶ Restore original</button>' : ""}</div></div>${editing ? passageEditorMarkup(segment) : `<p class="passage-text"></p>`}${timingNote(segment, expectedTiming)}</article>`;
        })
        .join("")
    : recording.active_transcript_id
      ? '<div class="transcript-empty"><h3>No passages in this result.</h3><p>Earlier text remains available in History.</p></div>'
      : '<div class="transcript-empty"><span class="upload-symbol" aria-hidden="true">▤</span><h3>The conversation starts here.</h3><p>Import a UCloud JSON or CSV transcript, or transcribe this recording using your configured provider.</p><p>Check Activity for preparation progress.</p></div>';
  activePassage = null;
  activeWord = null;
  for (const segment of recording.segments) {
    const passage = document.getElementById(`passage-${segment.id}`);
    if (!passage) continue;
    const input = passage.querySelector("textarea");
    if (input) {
      input.value = segment.text;
      renderEditorPlayback(segment, passage.querySelector(".passage-editor"));
      if (passage.querySelector("[data-timing-word]"))
        resetTimedWord(segment, passage, 0);
    } else
      renderPassageText(
        passage.querySelector(".passage-text"),
        segment,
        true,
        review.words.get(segment.id) || [],
      );
  }
  $("#transcript")
    .querySelectorAll(".speaker-badge")
    .forEach((badge) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "speaker-control";
      button.dataset.action = "speaker";
      button.setAttribute(
        "aria-label",
        `Change speaker for passage ${(indexesById.get(badge.closest("[data-segment]").dataset.segment) ?? -1) + 1}`,
      );
      badge.replaceWith(button);
      button.appendChild(badge);
    });
  highlightSearch(recording.segments, "passage-");
  refreshPassageRepairs();
  refreshComparisonAlignment();
  refreshMergeUndoOptions();
  updatePlaybackButtons();
  if (structureLocked()) {
    $("#transcript")
      .querySelectorAll("textarea, [data-action]")
      .forEach((element) => {
        element.dataset.structureDisabled = String(element.disabled);
        element.disabled = true;
      });
  }
  if (typeof renderManualReviews === "function") renderManualReviews();
}
$("#transcript-search").addEventListener("input", () => {
  if (state.comparison) {
    highlightSearch(state.recording.segments, "passage-");
    highlightSearch(state.comparison.segments, "compare-passage-");
  } else if (!state.dirty) {
    renderTranscript();
    renderComparison();
  }
});
$("#edit-mode").addEventListener("change", (event) => {
  if (structureLocked()) {
    event.target.value = state.mode;
    return;
  }
  if (!canLeave()) {
    event.target.value = state.mode;
    return;
  }
  clearEdit();
  state.mode = event.target.value;
  renderTranscript();
});
$("#transcript").addEventListener("input", (event) => {
  if (timedWordSaving || passageTextSaving) return;
  if (
    !event.target.matches(
      "textarea, [data-timing-text], [data-timing-start], [data-timing-end]",
    )
  )
    return;
  const segment = segmentFromNode(event.target);
  const editor = event.target.closest(".passage-editor");
  if (segment && editor) refreshPassageDraft(segment, editor);
});
function refreshCursorSplit(event) {
  const input = event?.target?.matches?.(".passage-editor textarea")
    ? event.target
    : document.activeElement;
  if (input?.matches(".passage-editor textarea"))
    refreshPassageSplit(input.closest(".passage-editor"));
}
for (const type of ["select", "keyup", "mouseup", "focusin"])
  $("#transcript").addEventListener(type, refreshCursorSplit);
document.addEventListener("selectionchange", refreshCursorSplit);
$("#transcript").addEventListener(
  "change",
  handle(async (event) => {
    if (timedWordSaving || passageTextSaving) return;
    const target = event.target;
    const editor = target.closest(".passage-editor");
    if (!editor) return;
    const segment = segmentFromNode(target);
    if (!segment) return;
    if (target.matches("[data-editor-realign]")) {
      passageRealignChoice = target.checked;
    } else if (target.matches("[data-editor-mode]")) {
      editor.querySelector('[data-editor-pane="text"]').hidden =
        target.value !== "text";
      const timing = editor.querySelector('[data-editor-pane="timing"]');
      editor.querySelector(".editor-playback-preview").hidden =
        !timing || target.value !== "timing";
      if (timing) {
        timing.hidden = target.value !== "timing";
        if (target.value === "timing")
          populateTimedWordChoices(segment, editor);
      }
    } else if (target.matches("[data-timing-word]")) {
      changeTimedWord(
        segment,
        editor,
        target.value === "" ? NaN : Number(target.value) - 1,
      );
    }
  }),
);
$("#transcript").addEventListener("focusin", (event) => {
  if (!event.target.matches("[data-timing-word]")) return;
  const segment = segmentFromNode(event.target);
  if (segment)
    populateTimedWordChoices(segment, event.target.closest(".passage-editor"));
});
function segmentFromNode(node) {
  return state.recording.segments.find(
    (s) => s.id === node.closest("[data-segment]")?.dataset.segment,
  );
}
$("#transcript").addEventListener("mousedown", (event) => {
  const replay = event.target.closest(
    '[data-action="editor-replay"], .editor-playback-preview [data-word]',
  );
  const editor = replay?.closest(".passage-editor");
  // Keep a text selection when replay is clicked with a pointer; keyboard
  // activation keeps focus on the button as usual.
  if (
    editor?.contains(document.activeElement) &&
    document.activeElement.matches("textarea, input")
  )
    event.preventDefault();
});
async function patchPassage(
  segment,
  values,
  context = recordingContext(),
  options = {},
) {
  if (!sameRecordingView(context)) return false;
  const input = $(`#passage-${segment.id} textarea`);
  if (input) input.disabled = true;
  saved("Saving…");
  const editVersionPath =
    values.mode === "free"
      ? values.realign === true
        ? "/edit-v1"
        : "/edit-v3"
      : "";
  const body = {
    revision: context.revision ?? state.recording.revision,
    ...values,
  };
  if (editVersionPath === "/edit-v3") delete body.realign;
  try {
    const recording = await api(
      `/recordings/${context.id}/segments/${segment.id}${editVersionPath}`,
      {
        method: "PATCH",
        body,
      },
    );
    if (!acceptRecording(context, recording)) return false;
    const originalEditor = options.editor;
    const keepFocus =
      originalEditor &&
      (document.activeElement === document.body ||
        originalEditor.contains(document.activeElement));
    const resumed =
      originalEditor &&
      state.editing === segment.id &&
      recording.segments.find((item) => item.id === segment.id);
    const realignChoice = originalEditor?.querySelector(
      "[data-editor-realign]",
    )?.checked;
    const correctionVisible =
      originalEditor?.querySelector("[data-word-correction]")?.hidden === false;
    clearEdit();
    renderEditorDetails();
    if (resumed && editableTimedWords(resumed)) {
      state.editing = resumed.id;
      passageRealignChoice = realignChoice ?? null;
    }
    renderTranscript();
    if (state.editing === segment.id) {
      const editor = document
        .getElementById(`passage-${segment.id}`)
        ?.querySelector(".passage-editor");
      if (editor && realignChoice !== undefined)
        editor.querySelector("[data-editor-realign]").checked = realignChoice;
      showTimedWordEditor(
        resumed,
        editor,
        options.wordIndex,
        keepFocus && correctionVisible,
        options.selection,
      );
      if (editor) {
        showWordCorrection(editor, correctionVisible);
        if (keepFocus && !correctionVisible)
          editor.querySelector("[data-timing-word]").focus();
      }
    }
    const warning =
      recording.timing_retention_warning || recording.realign_warning;
    if (typeof warning === "string" && warning) {
      const message = `Passage saved. ${warning}`;
      saved(
        recording.snapshot_error
          ? `${recording.snapshot_error} ${message}`
          : message,
      );
      toast(message, true);
    }
    return true;
  } catch (error) {
    if (sameRecordingView(context)) saved("Save failed — correction retained");
    throw error;
  } finally {
    if (input?.isConnected) input.disabled = false;
  }
}
$("#transcript").addEventListener(
  "click",
  handle(async (event) => {
    if (structureLocked()) return;
    if (
      (timedWordSaving || passageTextSaving) &&
      !event.target.closest('[data-action="editor-replay"]')
    )
      return;
    const segment = segmentFromNode(event.target);
    if (!segment) return;
    const word = event.target.closest("[data-word]");
    if (word) {
      seekTo(segment.words[Number(word.dataset.word)].start);
      return;
    }
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "merge-up" || action === "merge-down") {
      if (!canDiscardPassage()) return;
      const direction = action === "merge-up" ? "up" : "down";
      const target = manualEditTarget(
        `/recordings/${state.recording.id}/segments/${segment.id}/merge`,
        "POST",
        { direction },
      );
      if (!(await workingEditChoice(target, true))) return;
      const hadEditor = state.editing !== null || state.dirty;
      clearEdit();
      if (hadEditor) renderTranscript();
      else loadHints().catch(() => {});
      openPassageMerge(segment, direction);
    }
    if (action === "split") await splitPassage(segment);
    if (action === "delete") await deletePassage(segment);
    if (
      ["undo-merge", "undo-split"].includes(action) &&
      !structureLocked() &&
      !pendingResultMutations.has(state.recording?.id) &&
      canDiscardPassage()
    ) {
      const target = manualEditTarget(
        `/recordings/${state.recording.id}/segments/${segment.id}/${action}`,
        "POST",
      );
      if (!(await workingEditChoice(target, true))) return;
      await changePassageStructure(segment, action, {});
    }
    if (action === "retranscribe") await retranscribeNow([segment.id]);
    if (action === "seek") seekTo(segment.start);
    if (action === "speaker" && canDiscardPassage()) {
      const target = manualEditTarget(
        `/recordings/${state.recording.id}/segments/${segment.id}/speaker`,
        "PATCH",
      );
      if (!(await workingEditChoice(target, true))) return;
      openPassageSpeaker(segment);
    }
    if (action === "replay") {
      const window = replayWindow(segment);
      if (window) {
        seekTo(window.start);
        state.replayEnd = window.end;
        await media().play();
      }
    }
    if (action === "editor-replay") {
      const window = replayWindow(segment);
      if (!window || state.recording.status !== "ready") return;
      if (
        editorReplayId === segment.id &&
        state.replayEnd != null &&
        !media().paused
      ) {
        media().pause();
      } else {
        seekTo(window.start);
        state.replayEnd = window.end;
        editorReplayId = segment.id;
        await media().play();
      }
      updatePlaybackButtons();
    }
    if (action === "reset-timed-word") {
      const editor = event.target.closest(".passage-editor");
      const select = editor.querySelector("[data-timing-word]");
      resetTimedWord(segment, editor, Number(select.dataset.selected));
      refreshPassageDraft(segment, editor);
    }
    if (action === "edit-timed-word") {
      const editor = event.target.closest(".passage-editor");
      const visible = editor.querySelector("[data-word-correction]").hidden;
      showWordCorrection(editor, visible);
      if (visible) {
        const input = editor.querySelector("[data-timing-text]");
        input.focus();
        input.select();
      }
    }
    if (action === "previous-timed-word" || action === "next-timed-word") {
      const editor = event.target.closest(".passage-editor");
      const current = Number(
        editor.querySelector("[data-timing-word]").dataset.selected,
      );
      changeTimedWord(
        segment,
        editor,
        current + (action === "next-timed-word" ? 1 : -1),
      );
    }
    if (action === "save-timed-word") {
      const editor = event.target.closest(".passage-editor");
      const textarea = editor.querySelector("textarea");
      if (passageEditText(segment, textarea) !== segment.text) {
        toast("Save or cancel the passage text draft first.", true);
        return;
      }
      const select = editor.querySelector("[data-timing-word]");
      const text = editor.querySelector("[data-timing-text]");
      const start = editor.querySelector("[data-timing-start]");
      const end = editor.querySelector("[data-timing-end]");
      const index = Number(select.dataset.selected),
        original = segment.words[index];
      if (!original || !text.value.trim()) {
        toast("Enter a word before saving.", true);
        return;
      }
      const startValue = start.value === "" ? null : Number(start.value),
        endValue = end.value === "" ? null : Number(end.value),
        oldStart = Number.isFinite(original.start) ? original.start : null,
        oldEnd = Number.isFinite(original.end) ? original.end : null,
        timingChanged = startValue !== oldStart || endValue !== oldEnd;
      if (!timingChanged && text.value === original.word) {
        toast("There are no word changes to save.");
        return;
      }
      let values;
      if (!timingChanged) {
        values = {
          mode: segment.word_timing ? "word" : "mapped_word",
          word_index: index,
          text: text.value,
        };
      } else {
        if (!Number.isFinite(startValue) || !Number.isFinite(endValue)) {
          toast("Enter valid start and end times before saving.", true);
          return;
        }
        values = {
          mode: "timed_word",
          word_index: index,
          text: text.value,
          start: startValue,
          end: endValue,
        };
      }
      const context = {
        ...recordingContext(),
        revision: state.recording.revision,
      };
      const controls = [
        ...editor.querySelectorAll("textarea, input, select, button"),
      ].filter((control) => !control.matches('[data-action="editor-replay"]'));
      const disabled = controls.map((control) => control.disabled);
      timedWordSaving = true;
      controls.forEach((control) => (control.disabled = true));
      try {
        await patchPassage(segment, values, context, {
          editor,
          wordIndex: index,
          selection: [text.selectionStart, text.selectionEnd],
        });
      } finally {
        timedWordSaving = false;
        controls.forEach((control, index) => {
          if (control.isConnected) control.disabled = disabled[index];
        });
        if (typeof refreshManualReviewControls === "function")
          refreshManualReviewControls();
      }
    }
    if (action === "edit") {
      if (!canDiscardPassage()) return;
      const target = manualEditTarget(
        `/recordings/${state.recording.id}/segments/${segment.id}`,
        "PATCH",
        { mode: state.mode === "word" ? "word" : "free" },
      );
      if (!(await workingEditChoice(target, true))) return;
      const hadEditor = state.editing !== null || state.dirty;
      clearEdit();
      if (state.mode === "word") openWord(segment, 0);
      else {
        state.editing = segment.id;
        if (hadEditor || !openPassageEditorInPlace(segment)) {
          renderTranscript();
          $(`#passage-${segment.id} textarea`).focus();
        }
      }
    }
    if (
      action === "restore" &&
      canDiscardPassage() &&
      confirm("Restore this passage’s original text and imported word timings?")
    ) {
      pendingWorkingEditChoice = null;
      await patchPassage(segment, { mode: "restore" });
    }
    if (action === "cancel-edit") {
      pendingWorkingEditChoice = null;
      clearEdit();
      if (!closePassageEditorInPlace(segment)) renderTranscript();
    }
    if (action === "save-edit") {
      const button = event.target;
      const editor = button.closest(".passage-editor");
      if (
        passageEditText(segment, editor.querySelector("textarea")) ===
        segment.text
      )
        return;
      if (timedWordDraftChanged(segment, editor)) {
        toast("Save or reset the word changes first.", true);
        return;
      }
      const controls = [
        ...editor.querySelectorAll("textarea, input, select, button"),
      ].filter((control) => !control.matches('[data-action="editor-replay"]'));
      const disabled = controls.map((control) => control.disabled);
      passageTextSaving = true;
      controls.forEach((control) => (control.disabled = true));
      try {
        await patchPassage(segment, {
          mode: "free",
          text: passageEditText(segment, $(`#passage-${segment.id} textarea`)),
          realign: editor.querySelector("[data-editor-realign]").checked,
        });
      } finally {
        passageTextSaving = false;
        controls.forEach((control, index) => {
          if (control.isConnected) control.disabled = disabled[index];
        });
        if (typeof refreshManualReviewControls === "function")
          refreshManualReviewControls();
      }
    }
  }),
);
$("#transcript").addEventListener(
  "dblclick",
  handle(async (event) => {
    if (
      structureLocked() ||
      timedWordSaving ||
      passageTextSaving ||
      state.dirty
    )
      return;
    if (event.target.closest(".editor-playback-preview")) return;
    const word = event.target.closest("[data-word]");
    if (!word) return;
    const segment = segmentFromNode(word);
    if (!segment || !canDiscardPassage()) return;
    const target = manualEditTarget(
      `/recordings/${state.recording.id}/segments/${segment.id}`,
      "PATCH",
      { mode: "free" },
    );
    if (!(await workingEditChoice(target, true))) return;
    const hadEditor = state.editing !== null || state.dirty;
    clearEdit();
    state.editing = segment.id;
    if (hadEditor || !openPassageEditorInPlace(segment)) renderTranscript();
    const editor = document
      .getElementById(`passage-${segment.id}`)
      .querySelector(".passage-editor");
    editor.querySelector("[data-editor-mode]").value = "text";
    const input = editor.querySelector("textarea");
    input.focus();
    const clicked = segment.words?.[Number(word.dataset.word)];
    const characters = Array.from(segment.text);
    if (
      clicked &&
      Number.isInteger(clicked.char_start) &&
      Number.isInteger(clicked.char_end) &&
      clicked.char_start >= 0 &&
      clicked.char_end > clicked.char_start &&
      clicked.char_end <= characters.length &&
      characters.slice(clicked.char_start, clicked.char_end).join("") ===
        clicked.word &&
      input.value === segment.text.replace(/\r\n?/g, "\n")
    ) {
      // Word maps use code points; textarea selection uses UTF-16 and normalizes CRLF.
      const start = characters
        .slice(0, clicked.char_start)
        .join("")
        .replace(/\r\n?/g, "\n").length;
      input.setSelectionRange(
        start,
        start + clicked.word.replace(/\r\n?/g, "\n").length,
      );
    }
  }),
);
let passageStructurePending = false;
let passageStructureRecording = null;
// A pending structural change locks only the recording it changes; navigating to another
// recording keeps that one editable while the late reply is fenced by its own context.
function structureLocked() {
  return (
    passageStructurePending && passageStructureRecording === state.recording?.id
  );
}
let passageMergeContext = null;
async function changePassageStructure(
  segment,
  operation,
  values,
  context = recordingContext(),
  revision = state.recording.revision,
) {
  if (passageStructurePending) return;
  passageStructurePending = true;
  passageStructureRecording = context.id;
  const controls = [
    ...document.querySelectorAll(
      "#transcript textarea, #transcript [data-action], #transcript [data-review-status], #transcript [data-review-retry], [data-deleted-restore], #passage-merge-form button, #passage-merge-speaker",
    ),
  ];
  const disabled = controls.map((element) => element.disabled);
  controls.forEach((element) => {
    element.disabled = true;
  });
  saved("Saving…");
  try {
    const recording = await api(
      `/recordings/${context.id}/segments/${segment.id}/${operation}`,
      {
        method: "POST",
        body: { revision, result_id: context.result, ...values },
      },
    );
    if (!acceptRecording(context, recording)) return false;
    clearEdit();
    state.replayEnd = null;
    renderEditorDetails();
    renderTranscript();
    renderComparison();
    return true;
  } catch (error) {
    if (sameRecordingView(context)) saved("Save failed — passage unchanged");
    throw error;
  } finally {
    passageStructurePending = false;
    passageStructureRecording = null;
    controls.forEach((element, index) => {
      if (element.isConnected) element.disabled = disabled[index];
    });
    $("#transcript")
      .querySelectorAll("[data-structure-disabled]")
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
  }
}
async function deletePassage(segment) {
  if (
    structureLocked() ||
    pendingResultMutations.has(state.recording?.id) ||
    state.mode !== "free"
  )
    return;
  const context = recordingContext();
  const revision = state.recording.revision;
  const warning =
    "Delete this passage from the current result? Earlier text remains in History." +
    (state.dirty ? " Unsaved passage corrections will be discarded." : "");
  if (!confirm(warning) || !sameRecordingView(context)) return;
  pendingWorkingEditChoice = null;
  try {
    await changePassageStructure(segment, "delete", {}, context, revision);
  } catch (error) {
    if (sameRecordingView(context)) throw error;
  }
}
async function splitPassage(segment) {
  const input = $(`#passage-${segment.id} textarea`);
  if (!input || passageEditText(segment, input) !== segment.text)
    throw new Error("Save passage changes before splitting.");
  if (
    input.disabled ||
    structureLocked() ||
    timedWordSaving ||
    passageTextSaving
  )
    return;
  if (
    input.selectionStart !== input.selectionEnd ||
    !passageWordBoundary(input.value, input.selectionStart)
  )
    throw new Error(
      "Place the cursor between words, next to a space or line break.",
    );
  const cursor = passageCursorOffset(segment.text, input.selectionStart);
  const values = { cursor };
  if (!segment.word_timing && (segment.start != null || segment.end != null)) {
    const player = media(),
      position = player.currentTime;
    if (
      player.paused &&
      player.readyState >= 1 &&
      Number.isFinite(segment.start) &&
      Number.isFinite(segment.end) &&
      position > segment.start &&
      position < segment.end
    )
      values.split_time = position;
  }
  await changePassageStructure(segment, "split-v2", values);
}
function openPassageMerge(segment, direction) {
  const index = state.recording.segments.indexOf(segment);
  const neighbor =
    state.recording.segments[index + (direction === "up" ? -1 : 1)];
  if (!neighbor) return;
  passageMergeContext = {
    ...recordingContext(),
    revision: state.recording.revision,
    segment,
    direction,
  };
  const ordered =
    direction === "up" ? [neighbor, segment] : [segment, neighbor];
  $("#passage-merge-preview").textContent = ordered
    .map((item) => item.text)
    .join(" ");
  const speakers = [...new Set(ordered.map((item) => item.speaker))];
  $("#passage-merge-speaker").innerHTML = speakers
    .map(
      (id) =>
        `<option value="${h(id)}">${h(speakerName(state.recording, id))}</option>`,
    )
    .join("");
  $("#passage-merge-speaker-label").hidden = speakers.length === 1;
  $("#passage-merge-speaker").value = ordered[0].speaker;
  $("#passage-merge-error").hidden = true;
  $("#passage-merge-dialog").showModal();
}
$("#passage-merge-dialog").addEventListener("close", () => {
  passageMergeContext = null;
  pendingWorkingEditChoice = null;
});
$("#passage-merge-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const context = passageMergeContext;
  if (
    !context ||
    !sameRecordingView(context) ||
    state.recording.revision !== context.revision
  ) {
    $("#passage-merge-error").textContent =
      "The transcript changed. Close this dialog and choose the passages again.";
    $("#passage-merge-error").hidden = false;
    return;
  }
  try {
    if (
      await changePassageStructure(
        context.segment,
        "merge",
        {
          direction: context.direction,
          speaker_id: $("#passage-merge-speaker").value,
        },
        context,
        context.revision,
      )
    )
      $("#passage-merge-dialog").close();
  } catch (error) {
    if (sameRecordingView(context)) {
      $("#passage-merge-error").textContent = error.message;
      $("#passage-merge-error").hidden = false;
    }
  }
});
let passageSpeakerContext = null;
$("#speaker-dialog").addEventListener("close", () => {
  pendingWorkingEditChoice = null;
});
function openPassageSpeaker(segment) {
  editVersion++;
  passageSpeakerContext = {
    ...recordingContext(),
    segment: segment.id,
    revision: state.recording.revision,
  };
  // Voices of this result, plus names a researcher saved in it; never other results' labels
  // or app-generated enhancement defaults.
  const generated = ([id, name]) =>
    /^ENH_.+_MULTIPLE$/u.test(id) ||
    (/^ENH_[A-Za-z0-9]+_\d+$/u.test(id) &&
      name === `Speaker ${Number(id.split("_").at(-1))}`);
  const ids = [
    ...new Set([
      ...state.recording.segments.map((s) => s.speaker),
      ...Object.entries(state.recording.speakers)
        .filter((entry) => !generated(entry))
        .map(([id]) => id),
    ]),
  ];
  $("#passage-speaker-select").innerHTML =
    ids
      .map(
        (id) =>
          `<option value="${h(id)}">${h(speakerName(state.recording, id))}</option>`,
      )
      .join("") + '<option value="">＋ Add speaker…</option>';
  $("#passage-speaker-select").value = segment.speaker;
  $("#passage-speaker-name").value = "";
  $("#passage-speaker-name-label").hidden = true;
  $("#passage-speaker-name").required = false;
  $("#passage-speaker-select").disabled =
    $("#passage-speaker-name").disabled =
    $("#passage-speaker-save").disabled =
      false;
  $("#speaker-dialog").showModal();
}
$("#passage-speaker-select").addEventListener("change", (event) => {
  const add = !event.target.value;
  $("#passage-speaker-name-label").hidden = !add;
  $("#passage-speaker-name").required = add;
  if (add) $("#passage-speaker-name").focus();
});
$("#passage-speaker-form").addEventListener(
  "submit",
  handle(async (event) => {
    event.preventDefault();
    const context = passageSpeakerContext;
    if (!context || !sameRecordingView(context)) return;
    const button = event.submitter;
    button.disabled =
      $("#passage-speaker-select").disabled =
      $("#passage-speaker-name").disabled =
        true;
    try {
      const id = $("#passage-speaker-select").value;
      const recording = await api(
        `/recordings/${context.id}/segments/${context.segment}/speaker`,
        {
          method: "PATCH",
          body: {
            revision: context.revision,
            ...(id
              ? { speaker_id: id }
              : { name: $("#passage-speaker-name").value }),
          },
        },
      );
      const updateBadges =
        !state.comparison &&
        !state.editing &&
        !state.dirty &&
        state.recording?.revision === context.revision &&
        recording.revision === context.revision + 1;
      if (!acceptRecording(context, recording)) return;
      clearEdit();
      $("#speaker-dialog").close();
      renderEditorDetails();
      if (updateBadges) refreshSpeakerBadges(recording, context.revision);
      else renderTranscript();
    } finally {
      if (passageSpeakerContext === context)
        button.disabled =
          $("#passage-speaker-select").disabled =
          $("#passage-speaker-name").disabled =
            false;
    }
  }),
);
function openWord(segment, index) {
  editVersion++;
  wordContext = {
    ...recordingContext(),
    revision: state.recording.revision,
    segment,
  };
  $("#word-select").innerHTML = segment.words
    .map(
      (word, i) =>
        `<option value="${i}">${h(word.word)} · ${time(word.start)}</option>`,
    )
    .join("");
  $("#word-select").value = String(index);
  $("#word-input").value = segment.words[index].word;
  $("#word-select").disabled =
    $("#word-input").disabled =
    $("#word-form button[type=submit]").disabled =
      false;
  $("#word-dialog").showModal();
  $("#word-input").focus();
  $("#word-input").select();
}
$("#word-dialog").addEventListener("close", () => {
  pendingWorkingEditChoice = null;
});
$("#word-select").addEventListener("change", () => {
  $("#word-input").value =
    wordContext.segment.words[Number($("#word-select").value)].word;
});
$("#word-form").addEventListener(
  "submit",
  handle(async (event) => {
    event.preventDefault();
    const context = wordContext;
    if (!context || !sameRecordingView(context)) return;
    const button = event.submitter;
    button.disabled =
      $("#word-select").disabled =
      $("#word-input").disabled =
        true;
    try {
      if (
        await patchPassage(
          context.segment,
          {
            mode: "word",
            text: $("#word-input").value,
            word_index: Number($("#word-select").value),
          },
          context,
        )
      )
        $("#word-dialog").close();
    } finally {
      if (wordContext === context)
        button.disabled =
          $("#word-select").disabled =
          $("#word-input").disabled =
            false;
    }
  }),
);

function seekTo(value) {
  if (value == null || state.recording?.status !== "ready") return;
  state.replayEnd = null;
  editorReplayId = null;
  media().currentTime = value;
  updatePlaybackButtons();
}
async function togglePlayback() {
  if (state.recording?.status !== "ready") return;
  state.replayEnd = null;
  if (media().paused) await media().play();
  else media().pause();
}
$("#play-pause").addEventListener("click", handle(togglePlayback));
$("#play-transcript").addEventListener(
  "click",
  handle(async () => {
    if (state.recording?.status !== "ready") return;
    if (state.replayEnd != null) {
      state.replayEnd = null;
      await media().play();
      updatePlaybackButtons();
    } else await togglePlayback();
  }),
);
function updatePlaybackButtons() {
  const playing = !media().paused;
  $("#play-pause").textContent = playing ? "Ⅱ" : "▶";
  $("#play-pause").setAttribute("aria-label", playing ? "Pause" : "Play");
  $("#play-transcript").textContent =
    playing && state.replayEnd == null
      ? "Ⅱ Pause transcript"
      : "▶ Play transcript";
  const replay = $("#transcript").querySelector(".passage-editor-replay");
  if (replay) {
    const playingPassage =
      playing && state.replayEnd != null && editorReplayId === state.editing;
    replay.textContent = playingPassage
      ? "Ⅱ Pause passage"
      : `▷ ${replay.dataset.replayLabel}`;
  }
}
$("#skip-back").addEventListener("click", () =>
  seekTo(Math.max(0, media().currentTime - 5)),
);
$("#skip-forward").addEventListener("click", () =>
  seekTo(Math.min(media().duration || Infinity, media().currentTime + 5)),
);
$("#seek").addEventListener("input", (event) =>
  seekTo(Number(event.target.value)),
);
$("#playback-speed").addEventListener("change", (event) => {
  $("#audio").playbackRate = $("#video").playbackRate = Number(
    event.target.value,
  );
});
for (const player of [$("#audio"), $("#video")]) {
  player.addEventListener("play", () => {
    if (player === media()) {
      updatePlaybackButtons();
    }
  });
  player.addEventListener("pause", () => {
    if (player === media()) {
      updatePlaybackButtons();
    }
  });
  player.addEventListener("error", () => {
    if (player === media() && state.recording?.status === "ready")
      toast(
        "Playback could not load. Check Activity or retry media preparation.",
        true,
      );
  });
}
$("#view-mode").addEventListener(
  "change",
  handle(async (event) => {
    if (!state.recording) return;
    const previous = media(),
      position = previous.currentTime,
      playing = !previous.paused,
      speed = previous.playbackRate;
    cancelMediaHandoff();
    previous.pause();
    state.media = event.target.value;
    const next = media(),
      epoch = viewEpoch,
      recordingId = state.recording.id,
      generation = mediaHandoffGeneration,
      handoff = { next, cancel: null },
      current = () =>
        generation === mediaHandoffGeneration &&
        epoch === viewEpoch &&
        recordingId === state.recording?.id &&
        next === media();
    mediaHandoff = handoff;
    $("#video-stage").hidden = state.media !== "video";
    try {
      // Native media keeps later seek/rate/play/pause requests while metadata loads.
      // Never reapply these initial values after an asynchronous handoff.
      next.currentTime = position;
      next.playbackRate = speed;
      const pending = [];
      if (next.readyState < 1)
        pending.push(
          new Promise((resolve, reject) => {
            const finish = (error) => {
              clearTimeout(timer);
              next.removeEventListener("loadedmetadata", done);
              next.removeEventListener("error", failed);
              handoff.cancel = null;
              if (error) reject(error);
              else resolve();
            };
            const done = () => finish();
            const failed = () =>
              finish(new Error("This playback view could not load."));
            const timer = setTimeout(failed, 15000);
            handoff.cancel = done;
            next.addEventListener("loadedmetadata", done);
            next.addEventListener("error", failed);
          }),
        );
      if (playing) pending.push(next.play());
      else next.pause();
      await Promise.all(pending);
      if (!current()) return;
      updatePlaybackButtons();
    } catch (error) {
      if (current()) {
        if (error.name !== "AbortError") next.pause();
        throw error;
      }
    } finally {
      handoff.cancel?.();
      if (mediaHandoff === handoff) mediaHandoff = null;
    }
  }),
);
const playbackIndexes = new WeakMap();
function segmentAt(segments, position) {
  let index = playbackIndexes.get(segments);
  if (!index) {
    const rows = segments
      .flatMap((segment) => {
        if (
          Number.isFinite(segment.start) &&
          Number.isFinite(segment.end) &&
          segment.end > segment.start
        )
          return [{ start: segment.start, end: segment.end, segment }];
        // Missing passage bounds do not invalidate its reliable word intervals.
        // Index those intervals separately so unknown gaps never gain timing.
        return [...playbackWords(segment)].map((i) => ({
          start: segment.words[i].start,
          end: segment.words[i].end,
          segment,
        }));
      })
      .sort((a, b) => a.start - b.start);
    let maximum = -1;
    const ends = rows.map((s) => (maximum = Math.max(maximum, s.end)));
    index = { rows, ends };
    playbackIndexes.set(segments, index);
  }
  const { rows, ends } = index;
  let low = 0,
    high = rows.length - 1,
    found = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (rows[mid].start <= position) {
      found = mid;
      low = mid + 1;
    } else high = mid - 1;
  }
  for (let i = found; i >= 0 && ends[i] > position; i--)
    if (position < rows[i].end) return rows[i].segment;
  return null;
}
function revealPlaybackPassage(passage) {
  if (!passage) return;
  const pane = passage.closest(".transcript");
  if (!pane) return;
  let bounds = pane.getBoundingClientRect();
  const stackedComparison =
    pane.id === "comparison-transcript" &&
    Math.abs(bounds.left - $("#transcript").getBoundingClientRect().left) < 100;
  if (
    !stackedComparison &&
    (bounds.top > playbackBottom(pane) - 100 || bounds.bottom < 12)
  ) {
    window.scrollBy({ top: bounds.top - 12, behavior: "instant" });
    updatePlaybackViewport();
    bounds = pane.getBoundingClientRect();
  }
  const target = passage.getBoundingClientRect();
  const top = Math.max(12, bounds.top);
  const bottom =
    stackedComparison && bounds.top > playbackBottom(pane) - 100
      ? bounds.bottom
      : Math.min(bounds.bottom, playbackBottom(pane));
  // A long passage cannot fit at once; words are revealed as playback reaches them.
  const end = Math.min(target.bottom, target.top + bottom - top);
  if (target.top < top) {
    automaticTranscriptScrolls.add(pane);
    pane.scrollTop -= top - target.top;
  } else if (end > bottom) {
    automaticTranscriptScrolls.add(pane);
    pane.scrollTop += end - bottom;
  }
}
function playbackBottom(pane) {
  let bottom = window.innerHeight - 12;
  const bounds = pane.getBoundingClientRect();
  for (const overlay of [$("#player"), $("#video-stage")]) {
    if (overlay.hidden) continue;
    const box = overlay.getBoundingClientRect();
    if (box.left < bounds.right && box.right > bounds.left)
      bottom = Math.min(bottom, box.top - 12);
  }
  return bottom;
}
function updatePlaybackViewport() {
  if (!state.recording || $("#player").hidden) return;
  document.documentElement.style.setProperty(
    "--player-height",
    `${$("#player").getBoundingClientRect().height + 24}px`,
  );
  for (const pane of [$("#transcript"), $("#comparison-transcript")]) {
    if (!pane.getClientRects().length) continue;
    pane.style.maxHeight = `${Math.max(120, playbackBottom(pane) - Math.max(12, pane.getBoundingClientRect().top))}px`;
  }
}
let playbackViewportFrame = null;
function schedulePlaybackViewport(follow = false) {
  if (playbackViewportFrame !== null)
    cancelAnimationFrame(playbackViewportFrame);
  playbackViewportFrame = requestAnimationFrame(() => {
    playbackViewportFrame = null;
    updatePlaybackViewport();
    if (
      follow &&
      state.recording &&
      $("#follow-playback").checked &&
      !state.editing &&
      !media().paused
    ) {
      revealPlaybackPassage(activeWord || activePassage);
      if (state.comparison)
        revealPlaybackPassage(activeCompareWord || activeComparePassage);
    }
  });
}
const playbackViewportObserver = new ResizeObserver(() =>
  schedulePlaybackViewport(true),
);
playbackViewportObserver.observe($("#player"));
playbackViewportObserver.observe($("#video-stage"));
window.addEventListener("resize", () => schedulePlaybackViewport(true));
window.addEventListener("scroll", () => schedulePlaybackViewport(), {
  passive: true,
});
$("#follow-playback").addEventListener("change", (event) => {
  if (!event.target.checked || !state.recording) return;
  const position = media().currentTime || 0;
  const current = segmentAt(state.recording.segments, position);
  const passage = current ? $(`#passage-${current.id}`) : null;
  if (state.editing)
    followEditorPlaybackWord(passage?.querySelector(".word.current"));
  else
    revealPlaybackPassage(passage?.querySelector(".word.current") || passage);
  if (state.comparison) {
    const other = segmentAt(state.comparison.segments, position);
    const comparison = other ? $(`#compare-passage-${other.id}`) : null;
    if (!state.editing)
      revealPlaybackPassage(
        comparison?.querySelector(".word.current") || comparison,
      );
  }
});
function tickComparison(position, player) {
  if (!state.comparison) return;
  const segment = segmentAt(state.comparison.segments, position);
  const passage = segment ? $(`#compare-passage-${segment.id}`) : null;
  if (passage !== activeComparePassage) {
    activeComparePassage?.classList.remove("active");
    passage?.classList.add("active");
    activeComparePassage = passage;
    if (
      passage &&
      $("#follow-playback").checked &&
      !player.paused &&
      !state.editing
    )
      revealPlaybackPassage(passage);
  }
  const index = segment
    ? segment.words.findIndex(
        (w, i) =>
          playbackWords(segment).has(i) &&
          w.start <= position &&
          position < w.end,
      )
    : -1;
  const word =
    index >= 0 ? passage?.querySelector(`[data-word="${index}"]`) : null;
  if (word !== activeCompareWord) {
    activeCompareWord?.classList.remove("current");
    word?.classList.add("current");
    activeCompareWord = word;
    if (
      word &&
      $("#follow-playback").checked &&
      !player.paused &&
      !state.editing
    )
      revealPlaybackPassage(word);
  }
}
function tick() {
  if (state.recording) {
    const player = media(),
      position = player.currentTime || 0;
    if (state.replayEnd != null && position >= state.replayEnd) {
      player.pause();
      state.replayEnd = null;
    }
    $("#current-time").textContent = time(position);
    $("#seek").max = player.duration || state.recording.duration || 100;
    $("#seek").value = position;
    const segment = segmentAt(state.recording.segments, position);
    const passage = segment ? $(`#passage-${segment.id}`) : null;
    if (passage !== activePassage) {
      activePassage?.classList.remove("active");
      passage?.classList.add("active");
      activePassage = passage;
      if (
        passage &&
        $("#follow-playback").checked &&
        !state.editing &&
        !player.paused
      )
        revealPlaybackPassage(passage);
    }
    const index = segment
      ? segment.words.findIndex(
          (w, i) =>
            playbackWords(segment).has(i) &&
            w.start <= position &&
            position < w.end,
        )
      : -1;
    const word =
      index >= 0 ? passage?.querySelector(`[data-word="${index}"]`) : null;
    if (word !== activeWord) {
      activeWord?.classList.remove("current");
      word?.classList.add("current");
      activeWord = word;
      if (word && $("#follow-playback").checked && !player.paused) {
        if (state.editing) followEditorPlaybackWord(word);
        else revealPlaybackPassage(word);
      }
    }
    tickComparison(position, player);
  }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
document.addEventListener(
  "keydown",
  handle(async (event) => {
    if (
      !state.recording ||
      event.target.closest("input,textarea,select,button") ||
      $("dialog[open]")
    )
      return;
    if (event.code === "Space") {
      event.preventDefault();
      await togglePlayback();
    }
    if (event.altKey && event.code === "ArrowLeft") {
      event.preventDefault();
      seekTo(Math.max(0, media().currentTime - 5));
    }
    if (event.altKey && event.code === "ArrowRight") {
      event.preventDefault();
      seekTo(media().currentTime + 5);
    }
  }),
);
window.addEventListener("beforeunload", (event) => {
  if (
    pendingBulkLanguage ||
    state.dirty ||
    projectSettingsDirty() ||
    recordingNotesDirty()
  ) {
    event.preventDefault();
    event.returnValue = "";
  }
});

function providerTab(settings) {
  if (settings.provider_type) return settings.provider_type;
  return settings.base_url === OPENAI_URL &&
    Object.values(presets).some((p) => p.model === settings.model)
    ? "openai"
    : "custom";
}
const nativeFields = [
  ["project", "project_id", ""],
  ["template", "template_job_id", ""],
  ["storage", "storage_path", ""],
  ["hours", "hours", 24],
  ["input", "input_parameter", "input_file"],
  ["output", "output_parameter", "output_dir"],
  ["model-param", "model_parameter", "model"],
  ["language-param", "language_parameter", "language"],
  ["format", "format_parameter", "output_format"],
];
function nativeSetup() {
  return {
    ...(state.nativeResolved || {}),
    ...Object.fromEntries(
      nativeFields.map(([id, field]) => [
        field,
        id === "hours"
          ? Number($(`#native-${id}`).value)
          : $(`#native-${id}`).value.trim(),
      ]),
    ),
    cleanup_after_success: $("#native-cleanup").checked,
  };
}
function applyNativeSetup(setup) {
  state.nativeResolved = setup;
  for (const [id, field, fallback] of nativeFields)
    $(`#native-${id}`).value = setup[field] ?? fallback;
}
function nativeReady(setup) {
  const product = setup.product;
  return !!(
    setup.project_id &&
    setup.storage_path &&
    (product?.id === "gpu-nvidia-b200-1-gpu" ||
      (product?.id === "gpu-nvidia-b200-1-mig.1g" &&
        product.category === "gpu-nvidia-b200" &&
        product.provider === "ucloud")) &&
    (setup.template_job_id || setup.application)
  );
}
function connectionAllocation() {
  // A connection recheck must not rewrite the identity that existing jobs approved.
  return state.settingsProvider
    ? nativeSetup().product?.id === "gpu-nvidia-b200-1-gpu"
      ? "full"
      : "mig1g"
    : "mig1g";
}
async function connectNative() {
  if (!$("#native-model-choice").reportValidity()) return false;
  const token = ++modelCheckToken;
  const context = providerContext();
  const button = $("#connect-ucloud");
  button.disabled = true;
  $("#ucloud-check-results").textContent =
    "Finding UCloud project, Transcriber Batch, GPU and storage…";
  try {
    const result = await api("/providers/discover-ucloud", {
      method: "POST",
      body: {
        provider_type: "ucloud",
        base_url: $("#base-url").value,
        model: $("#model").value,
        language: $("#language").value,
        api_key: $("#api-key").value || null,
        ucloud_transcriber: nativeSetup(),
        native_allocation_choice: nativeSetup().template_job_id
          ? null
          : connectionAllocation(),
      },
    });
    if (token !== modelCheckToken || !sameProvider(context)) return false;
    applyNativeSetup(result.setup);
    renderEnabledModels(true);
    state.nativeChoices = result.choices || {};
    $("#native-discovery-choices").innerHTML = Object.entries(
      state.nativeChoices,
    )
      .filter(([field, choices]) => field !== "product" && choices.length)
      .map(
        ([
          field,
          choices,
        ]) => `<label>${h({ project_id: "Project", storage_path: "Storage", application: "Transcriber application/version" }[field] || field)}
        <select data-native-choice="${h(field)}"><option value="">Choose…</option>
        ${choices.map((c, i) => `<option value="${i}">${h(c.name)}</option>`).join("")}</select></label>`,
      )
      .join("");
    $("#ucloud-check-results").textContent = result.note;
    return result.ready;
  } catch (error) {
    if (token === modelCheckToken && sameProvider(context))
      $("#ucloud-check-results").replaceChildren();
    throw error;
  } finally {
    button.disabled = false;
  }
}
$("#connect-ucloud").addEventListener("click", handle(connectNative));
$("#native-discovery-choices").addEventListener(
  "change",
  handle(async (event) => {
    const field = event.target.dataset.nativeChoice;
    if (!field || event.target.value === "") return;
    const chosen = state.nativeChoices[field][Number(event.target.value)];
    const setup = nativeSetup();
    if (field === "application") {
      const split = chosen.id.lastIndexOf("@");
      setup.application = {
        name: chosen.id.slice(0, split),
        version: chosen.id.slice(split + 1),
      };
    } else setup[field] = chosen.id;
    if (field === "project_id") {
      setup.storage_path = "";
      setup.application = null;
      setup.product = null;
      setup.template_job_id = "";
    }
    applyNativeSetup(setup);
    await connectNative();
  }),
);
for (const [id] of nativeFields) {
  $(`#native-${id}`).addEventListener("input", () => {
    clearModelCheck();
    if (id === "project") renderEnabledModels(true);
  });
}
$("#check-ucloud").addEventListener("click", async () => {
  const token = ++modelCheckToken;
  const button = $("#check-ucloud");
  button.disabled = true;
  $("#ucloud-check-results").textContent =
    "Checking UCloud template and storage…";
  try {
    const result = await api("/providers/check-ucloud", {
      method: "POST",
      body: {
        provider_type: "ucloud",
        base_url: $("#base-url").value,
        model: $("#model").value,
        language: $("#language").value,
        api_key: $("#api-key").value || null,
        ucloud_transcriber: nativeSetup(),
      },
    });
    if (token !== modelCheckToken) return;
    $("#native-parameter-details").open = Boolean(result.issue);
    $("#ucloud-check-results").innerHTML =
      `<p>${h(result.application.name)} ${h(result.application.version)} · ${h(result.product.id)}</p><p>${h(result.issue || result.note)}</p><ul>${result.parameters.map((p) => `<li>${h(p.name)}: ${h(p.type)}${p.options?.length ? ` · ${h(p.options.map((o) => o.value).join(", "))}` : ""}</li>`).join("")}</ul>`;
  } catch (error) {
    if (token === modelCheckToken)
      $("#ucloud-check-results").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
let modelCheckToken = 0;
let providerFormVersion = 0;
let providerFormProfile = null;
function providerContext() {
  return {
    version: providerFormVersion,
    id: state.settingsProvider,
    tab: state.settingsTab,
  };
}
function sameProvider(context) {
  return (
    $("#settings-dialog").open &&
    context.version === providerFormVersion &&
    context.id === state.settingsProvider &&
    context.tab === state.settingsTab
  );
}
for (const event of ["input", "change"])
  $("#settings-form").addEventListener(
    event,
    (event) => {
      // These controls save separately and must not invalidate provider drafts.
      if (
        ["enhance-prepare-ucloud", "runtime-max-allocations"].includes(
          event.target.id,
        )
      )
        return;
      providerFormVersion++;
      $("#provider-error").hidden = true;
    },
    true,
  );
$("#settings-form").addEventListener(
  "invalid",
  (event) => {
    event.target.closest("details")?.setAttribute("open", "");
    if ($("#provider-advanced").contains(event.target))
      $("#provider-advanced").open = true;
  },
  true,
);
function renderCatalog() {
  const language = $("#catalog-language").value;
  const models = (state.catalog?.models || []).filter(
    (m) => language === "all" || m.languages.includes(language),
  );
  $("#catalog-model").innerHTML =
    `<option value="">${h($("#hf-model").value ? `Custom: ${$("#hf-model").value}` : "Custom model / revision")}</option>` +
    models
      .map(
        (m) =>
          `<option value="${h(m.id)}">${h(m.name)} · ${h(m.status)}</option>`,
      )
      .join("");
  const entry = models.find(
    (m) =>
      m.model === $("#hf-model").value &&
      m.revision === $("#hf-revision").value,
  );
  $("#catalog-model").value = entry?.id || "";
  $("#catalog-details").innerHTML = entry
    ? `<p>${h(entry.notes)}</p><p>${h(entry.languages.join(" / "))} · ${h(entry.license)} · ${h(entry.status)} · ${h(entry.parameters.toLocaleString())} parameters</p><p>Revision: <code>${h(entry.revision)}</code></p><p><a href="${h(entry.model_card)}" target="_blank" rel="noopener noreferrer">Pinned model card</a> · <a href="${h(entry.parameter_source)}" target="_blank" rel="noopener noreferrer">Parameter provenance</a></p><p>Runtime and allocation unvalidated; response support must be checked when deployed.</p>`
    : "Enter an owner/model ID and a revision. Custom configurations are not validated by this catalog.";
}
$("#catalog-language").addEventListener("change", renderCatalog);
function syncHfModel() {
  const previous = $("#model").value;
  const next = $("#hf-model").value.trim();
  if (
    !$("#provider-name").value ||
    ["UCloud vLLM · Speech", `UCloud vLLM ${previous}`].includes(
      $("#provider-name").value,
    )
  )
    $("#provider-name").value = `UCloud vLLM ${next}`;
  $("#model").value = next;
  clearModelCheck();
  renderCatalog();
}
$("#hf-model").addEventListener("input", syncHfModel);
$("#hf-revision").addEventListener("input", renderCatalog);
$("#catalog-model").addEventListener("change", (event) => {
  const model = state.catalog.models.find((m) => m.id === event.target.value);
  if (!model) {
    $("#provider-advanced").open = true;
    $("#hf-model").focus();
    return;
  }
  $("#hf-model").value = model.model;
  $("#hf-revision").value = model.revision;
  $("#capability").value = model.suggested_response;
  syncHfModel();
  capabilityHelp();
});
function clearModelCheck() {
  modelCheckToken++;
  $("#model-check-results").replaceChildren();
  $("#ucloud-check-results").replaceChildren();
}
function showProviderTab(tab) {
  providerFormVersion++;
  $("#provider-error").hidden = true;
  state.settingsTab = tab;
  $(".provider-manager").hidden = ["openai", "ucloud"].includes(tab);
  document.querySelectorAll("[data-provider-tab]").forEach((button) => {
    const selected = button.dataset.providerTab === tab;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  $("#hf-panel").hidden = true;
  $("#hf-panel").disabled = true;
  $("#processing-panel").hidden = true;
  $("#processing-panel").disabled = true;
  $("#provider-fields").hidden = false;
  $("#provider-fields").disabled = false;
  $("#provider-fields").setAttribute("aria-labelledby", `tab-${tab}`);
  $("#ucloud-panel").hidden = tab !== "ucloud";
  $("#ucloud-panel").disabled = tab !== "ucloud";
  // A hidden CBS Runtime has no panels and no readiness probes (ADR 0145).
  const runtime = tab === "ucloud" && featureOn("cbs_runtime");
  $("#runtime-preparation-panel").hidden = !runtime;
  $("#runtime-preparation-panel").disabled = !runtime;
  $("#runtime-capacity-panel").hidden = !runtime;
  if (!runtime) $("#runtime-max-allocations").disabled = true;
  if (runtime) {
    $("#enhance-cloud-check").disabled = true;
    $("#enhance-prepare-ucloud").disabled = true;
    queueMicrotask(() => {
      if ($("#settings-dialog").open && state.settingsTab === "ucloud")
        showCloudRuntime().catch(() => {});
      if ($("#settings-dialog").open && state.settingsTab === "ucloud")
        loadRuntimeCapacity().catch(() => {});
    });
  }
  $("#ucloud-check-results").replaceChildren();
  $("#response-options").hidden = tab === "ucloud";
  $("#parallel-options").hidden = tab === "ucloud";
  $("#base-url-title").textContent =
    tab === "ucloud"
      ? "UCloud server"
      : tab === "ucloud-vllm"
        ? "Running speech endpoint (optional)"
        : "API base URL";
  $("#base-url-label").hidden = tab === "openai";
  $("#provider-advanced").open = false;
  $("#model-value-label").hidden = tab !== "custom";
  $("#provider-preset-label").hidden = true;
  $("#native-model-label").hidden = true;
  $("#language-label").hidden = ["ucloud", "openai"].includes(tab);
  $("#language").disabled = ["ucloud", "openai"].includes(tab);
  $("#openai-model-check").hidden = tab !== "openai";
  $("#vllm-model-panel").hidden = tab !== "ucloud-vllm";
  $("#saved-provider-title").textContent =
    tab === "ucloud-vllm" ? "Saved models" : "Saved provider";
  $("#new-provider").textContent =
    tab === "ucloud-vllm" ? "＋ Add model" : "＋ Add provider";
  $("#ucloud-vllm-setup").hidden = tab !== "ucloud-vllm";
  $("#model-field-label").textContent =
    tab === "ucloud-vllm"
      ? "Served model ID (same as Hugging Face model)"
      : "Model";
  $("#base-url").required = tab !== "ucloud-vllm";
  $("#base-url").placeholder =
    tab === "ucloud-vllm"
      ? "https://YOUR-UCLOUD-HOST/v1 (once deployed)"
      : "https://api.openai.com/v1";
  $("#serving-key-label").textContent =
    tab === "ucloud"
      ? "UCloud API token"
      : tab === "ucloud-vllm"
        ? "Serving API key (when deployed)"
        : "API key";
  $("#ucloud-token").value = "";
  $("#hf-token").value = "";
  $("#model").readOnly = tab !== "custom";
  $("#hf-model").required = tab === "ucloud-vllm";
  $("#capability").disabled = tab === "openai";
  $("#api-key").value = "";
  layoutProvider(tab);
  clearModelCheck();
}
function layoutProvider(tab) {
  const basic = $("#provider-basic"),
    advanced = $("#provider-advanced");
  // "Forget saved key" stays on its own row right under the key box (all tabs).
  basic.append(
    $("#api-key-label"),
    $("#key-actions"),
    $("#provider-preset-label"),
    $("#base-url-label"),
    $("#model-value-label"),
    $("#catalog-model").closest("label"),
    $("#ucloud-vllm-setup"),
    $("#ucloud-panel"),
    $("#runtime-preparation-panel"),
    $("#openai-model-check"),
    $("#provider-model-options"),
  );
  advanced.append($("#provider-name-label"), $("#language-label"));
  $("#provider-name-label").hidden = true;
  $("#provider-name").disabled = true;
  $("#provider-name").required = false;
  if (tab !== "custom") advanced.append($("#base-url-label"));
  if (tab === "ucloud-vllm")
    advanced.append($("#api-key-label"), $("#key-actions"));
  advanced.append(
    $("#remember-key-label"),
    $("#native-advanced"),
    $("#vllm-account-fields"),
    $("#vllm-model-panel"),
    $("#response-options"),
    $("#parallel-options"),
    $("#capability-help"),
    $(".provider-approvals"),
    $("#forget-hosting-keys"),
  );
  $("#provider-model-options").hidden = !["openai", "ucloud"].includes(tab);
  $("#provider-model-options").disabled = !["openai", "ucloud"].includes(tab);
  $("#catalog-model").closest("label").hidden = tab !== "ucloud-vllm";
  for (const id of [
    "native-advanced",
    "vllm-model-panel",
    "ucloud-vllm-setup",
  ]) {
    const active =
      id === "native-advanced" ? tab === "ucloud" : tab === "ucloud-vllm";
    $(`#${id}`).hidden = !active;
    $(`#${id}`).disabled = !active;
  }
  $("#vllm-account-fields").hidden = tab !== "ucloud-vllm";
  $("#vllm-account-fields")
    .querySelectorAll("input")
    .forEach((input) => {
      input.disabled = tab !== "ucloud-vllm";
    });
  $("#forget-hosting-keys").hidden = tab !== "ucloud-vllm";
}
function providerSummary() {
  const capability = {
    words: "Word timing included",
    speakers: "Speaker labels included",
    words_speakers: "Word timing and speaker labels included",
    segments: "Passage timing",
    json: "Text output; timing and speakers can be added afterward",
  }[$("#capability").value];
  $("#provider-summary").hidden = ["openai", "ucloud"].includes(
    state.settingsTab,
  );
  $("#provider-summary").textContent =
    state.settingsTab === "ucloud-vllm" && !$("#base-url").value
      ? "Awaiting deployment · Saving starts no hosting job or model download."
      : capability;
  $("#provider-policy-summary").textContent =
    `Protected projects: ${$('[name="approved-for"]').checked ? "Allowed" : "Not allowed"}. Change in Advanced settings.`;
}
$('[name="approved-for"]').addEventListener("change", providerSummary);
let hfVersion = 0;
// Check token covers the whole speaker-label preparation (ADR 0057): the token, the gated
// model's terms, and a verified copy of the model on this computer.
function hfStatusLines(status) {
  const local = {
    verified: "Speaker model: downloaded and verified on this computer.",
    missing:
      "Speaker model: not on this computer yet. Check token downloads it.",
    failed: `Speaker model: ${status.speaker_model_error || "download failed."}`,
  }[status.speaker_model_local];
  if (!status.has_token)
    return [
      "No token saved. Word timing works without one; speaker labels need the speaker model.",
      ...(status.speaker_model_local === "verified" ? [local] : []),
    ];
  const token = {
    unchecked:
      "Token saved (not checked). Check token also prepares speaker labels.",
    valid: "Token valid.",
    invalid: "Hugging Face rejected this token.",
    unavailable: "Hugging Face could not be reached; token not verified.",
  }[status.token_status];
  const access =
    status.token_status === "valid"
      ? {
          allowed: `Speaker model terms accepted (${status.speaker_model}).`,
          denied: `Accept the ${status.speaker_model} terms on Hugging Face with this token's account, then Check token again.`,
          unavailable: "Speaker model terms could not be checked.",
        }[status.speaker_model_access]
      : null;
  const showLocal =
    status.speaker_model_local === "verified" ||
    (status.token_status === "valid" &&
      status.speaker_model_access === "allowed");
  return [token, access, showLocal ? local : null].filter(Boolean);
}
function renderHfStatus(status) {
  $("#hf-settings-status").textContent = status.has_token
    ? "Token saved"
    : "No token saved";
  $("#hf-settings-forget").hidden = !status.has_token;
  const result = $("#hf-settings-result");
  result.replaceChildren();
  hfStatusLines(status).forEach((line, index) => {
    if (index) result.append(document.createElement("br"));
    result.append(line);
  });
  if (
    status.token_status === "valid" &&
    status.speaker_model_access === "denied"
  ) {
    const link = document.createElement("a");
    link.href = `https://huggingface.co/${status.speaker_model}`;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "Open the model page ↗";
    result.append(" ", link);
  }
}
async function showHuggingFaceTab() {
  const version = ++hfVersion;
  providerFormVersion++;
  $("#provider-error").hidden = true;
  state.settingsTab = "huggingface";
  document.querySelectorAll("[data-provider-tab]").forEach((button) => {
    const selected = button.dataset.providerTab === "huggingface";
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  $("#provider-fields").hidden = true;
  $("#provider-fields").disabled = true;
  $("#processing-panel").hidden = true;
  $("#processing-panel").disabled = true;
  $("#hf-panel").hidden = false;
  $("#hf-panel").disabled = true;
  $("#hf-settings-token").value = "";
  $("#hf-settings-result").textContent = "Loading…";
  const context = providerContext();
  const status = await api("/huggingface-settings").catch((error) => {
    if (version === hfVersion && sameProvider(context))
      $("#hf-settings-result").textContent =
        "Select Hugging Face again to retry.";
    throw error;
  });
  if (version !== hfVersion || !sameProvider(context)) return;
  renderHfStatus(status);
  $("#hf-panel").disabled = false;
}
async function hfRequest(button, path, method) {
  const version = ++hfVersion;
  const token = $("#hf-settings-token").value.trim();
  button.disabled = true;
  $("#hf-settings-result").textContent =
    method === "POST"
      ? "Checking the token and speaker model terms; downloading the speaker model if needed (about 33 MB)…"
      : "Saving…";
  try {
    const status = await api(path, {
      method,
      body: {
        hf_token: token || null,
        remember: $("#hf-settings-remember").checked,
      },
    });
    if (version !== hfVersion || state.settingsTab !== "huggingface") return;
    if (method === "PUT") $("#hf-settings-token").value = "";
    renderHfStatus(status);
  } catch (error) {
    if (version === hfVersion) $("#hf-settings-result").textContent = "";
    throw error;
  } finally {
    button.disabled = false;
  }
}
$("#hf-settings-save").addEventListener(
  "click",
  handle((event) =>
    hfRequest(event.currentTarget, "/huggingface-settings", "PUT"),
  ),
);
$("#hf-settings-forget").addEventListener(
  "click",
  handle(async (event) => {
    if (
      !confirm(
        "Forget the saved Hugging Face token? Cached speaker models stay usable offline.",
      )
    )
      return;
    const button = event.currentTarget,
      version = ++hfVersion;
    button.disabled = true;
    try {
      const status = await api("/huggingface-settings", {
        method: "PUT",
        body: { hf_token: "", remember: true },
      });
      if (version === hfVersion && state.settingsTab === "huggingface") {
        $("#hf-settings-token").value = "";
        renderHfStatus(status);
      }
    } finally {
      button.disabled = false;
    }
  }),
);
$("#hf-settings-check").addEventListener(
  "click",
  handle((event) =>
    hfRequest(event.currentTarget, "/huggingface-settings/check", "POST"),
  ),
);
$("#settings-dialog").addEventListener("close", () => {
  hfVersion++;
  $("#hf-settings-token").value = "";
});
// Settings → Processing (ADR 0059): checked when opened, saved on change.
let processingVersion = 0,
  processingSettings = null,
  runtimeCapacity = null,
  localCheckVersion = 0,
  processingDraftVersion = 0,
  processingSaveVersion = 0,
  processingSaveTail = Promise.resolve(),
  capacityViewVersion = 0,
  capacitySaveVersion = 0,
  capacitySaveTail = Promise.resolve();
function processingContext() {
  return { version: processingVersion, id: state.settingsProvider };
}
function sameProcessing(context) {
  return (
    $("#settings-dialog").open &&
    state.settingsTab === "processing" &&
    context.version === processingVersion &&
    context.id === state.settingsProvider
  );
}
function renderLocalRuntime(status) {
  const settings = processingSettings || {};
  const gpu = status.gpu || {};
  const runtime = status.error
    ? status.error
    : status.ready
      ? `WhisperX ${status.version} is ready on this computer.`
      : status.unsupported_platform
        ? status.note
        : `WhisperX ${status.expected_version} is missing from this Python runtime; run uv sync --locked in the app folder.`;
  const hardware = !gpu.checked
    ? "GPU detection is unavailable in this runtime."
    : gpu.available
      ? `GPU found: ${gpu.devices.map((d) => `${d.name} (${d.memory_gb} GB)`).join(", ")}.`
      : "No CUDA GPU found.";
  const noGpu = gpu.checked === true && gpu.available === false;
  $("#enhance-device").disabled = noGpu;
  $("#rule-cuda-factor").disabled = noGpu;
  $("#enhance-device").value = noGpu ? "cpu" : settings.device || "auto";
  $("#enhance-device").title = noGpu
    ? "No CUDA GPU was detected. Your saved device preference is retained for a later GPU check."
    : "";
  $("#rule-cuda-factor").title = noGpu
    ? "No CUDA GPU was detected. The saved multiplier is retained for a later GPU check."
    : "";
  const device =
    settings.device === "cuda" && noGpu
      ? " The saved CUDA preference is unavailable; local jobs requiring CUDA cannot run."
      : settings.device === "auto" && gpu.checked
        ? ` Automatic uses the ${gpu.available ? "GPU" : "CPU"}.`
        : settings.device === "cpu" && gpu.available
          ? " Choose Automatic to use the GPU."
          : "";
  $("#processing-local-status").textContent = `${runtime} ${hardware}${device}`;
}
async function checkLocalRuntime() {
  const version = ++localCheckVersion,
    context = processingContext(),
    settings = { ...processingSettings },
    button = $("#enhance-check");
  const current = () =>
    version === localCheckVersion &&
    sameProcessing(context) &&
    settings.runtime_python === $("#enhance-python").value.trim();
  button.disabled = true;
  $("#enhance-device").disabled = false;
  $("#rule-cuda-factor").disabled = false;
  $("#enhance-device").value = processingSettings?.device || "auto";
  $("#processing-local-status").textContent =
    "Checking this computer's runtime and GPU…";
  try {
    const status = await api("/enhancement-runtime/check", {
      method: "POST",
      body: settings,
      background: true,
    });
    if (current()) renderLocalRuntime(status);
  } catch (error) {
    if (current()) $("#processing-local-status").textContent = error.message;
    throw error;
  } finally {
    if (version === localCheckVersion) button.disabled = false;
  }
}
async function showProcessingTab() {
  const version = ++processingVersion;
  providerFormVersion++;
  $("#provider-error").hidden = true;
  state.settingsTab = "processing";
  document.querySelectorAll("[data-provider-tab]").forEach((button) => {
    const selected = button.dataset.providerTab === "processing";
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  for (const id of ["#provider-fields", "#hf-panel"]) {
    $(id).hidden = true;
    $(id).disabled = true;
  }
  $("#processing-panel").hidden = false;
  $("#processing-panel").disabled = true;
  $("#processing-status").textContent = "";
  const context = providerContext();
  // Read after this page's Processing writes, including saves accepted before closing.
  await Promise.all([
    processingSaveTail.catch(() => {}),
    routingSaveTail.catch(() => {}),
  ]);
  if (version !== processingVersion || !sameProvider(context)) return;
  const [settings, local] = await Promise.all([
    api("/enhancement-settings"),
    api("/local-runtime"),
  ]).catch((error) => {
    if (version === processingVersion && sameProvider(context)) {
      $("#processing-status").textContent =
        "Select Local Processing again to retry.";
    }
    throw error;
  });
  if (version !== processingVersion || !sameProvider(context)) return;
  processingSettings = {
    runtime_python: settings.runtime_python,
    device: settings.device,
    language: settings.language,
    download_models: settings.download_models,
    prepare_ucloud_models: settings.prepare_ucloud_models === true,
  };
  $("#enhance-python").value = settings.runtime_python;
  $("#enhance-device").value = settings.device;
  $("#enhance-device").disabled = false;
  $("#rule-cuda-factor").disabled = false;
  $("#enhance-downloads").checked = settings.download_models;
  $("#processing-panel").disabled = false;
  // The startup check is reused; only a first visit without one checks now.
  if (local.status) renderLocalRuntime(local.status);
  else checkLocalRuntime().catch(() => {});
  loadLocalConcurrency().catch(() => {});
  await renderCloudEnhancementStatus().catch(() => null);
}
// Parallel local tasks per step (ADR 0146): a value equal to this computer's automatic
// default is saved as automatic, so it follows the library to another computer.
const concurrencyLanes = ["media", "inspection", "api", "admission"];
let localConcurrency = null,
  concurrencyViewVersion = 0,
  concurrencySaveVersion = 0,
  concurrencyDraftVersion = 0,
  concurrencySaveTail = Promise.resolve();
function renderLocalConcurrency(status) {
  localConcurrency = status;
  for (const lane of concurrencyLanes) {
    const input = $(`#concurrency-${lane}`);
    input.max = status.maximum[lane];
    input.value = status.effective[lane];
    input.disabled = false;
  }
  const labels = {
    media: "playback",
    inspection: "inspection",
    api: "provider requests",
    admission: "preparation checks",
  };
  const describe = (values, lanes) =>
    lanes.map((lane) => `${labels[lane]} ${values[lane]}`).join(", ");
  const custom = concurrencyLanes.filter((lane) => status.saved[lane] !== null);
  $("#concurrency-note").textContent =
    `Automatic on this computer (${status.logical_cpus} logical processors): ` +
    `${describe(status.defaults, concurrencyLanes)}. ` +
    (custom.length
      ? `Your own values: ${describe(status.saved, custom)}. `
      : "") +
    "Word timing and speakers on this computer still run one recording at a time; " +
    "UCloud limits are set under UCloud.";
}
async function loadLocalConcurrency() {
  const version = ++concurrencyViewVersion,
    context = processingContext();
  for (const lane of concurrencyLanes)
    $(`#concurrency-${lane}`).disabled = true;
  $("#concurrency-status").textContent = "";
  await concurrencySaveTail.catch(() => {});
  const status = await api("/local-concurrency", { background: true }).catch(
    (error) => {
      if (version === concurrencyViewVersion && sameProcessing(context))
        $("#concurrency-status").textContent =
          "Parallel task settings could not be loaded. Select Local Processing again to retry.";
      throw error;
    },
  );
  if (version !== concurrencyViewVersion || !sameProcessing(context)) return;
  renderLocalConcurrency(status);
}
async function saveLocalConcurrency(automatic) {
  if (!localConcurrency) return;
  const body = {};
  for (const lane of concurrencyLanes) {
    const input = $(`#concurrency-${lane}`);
    if (!automatic && !input.checkValidity()) {
      $("#concurrency-status").textContent =
        `Choose a whole number from 1 to ${input.max} for each step.`;
      input.focus();
      return;
    }
    const value = Number(input.value);
    body[lane] =
      automatic || value === localConcurrency.defaults[lane] ? null : value;
  }
  const version = ++concurrencySaveVersion,
    draft = concurrencyDraftVersion,
    context = processingContext();
  const current = () =>
    version === concurrencySaveVersion &&
    draft === concurrencyDraftVersion &&
    sameProcessing(context);
  $("#concurrency-status").textContent = "Saving…";
  const save = concurrencySaveTail
    .catch(() => {})
    .then(() => api("/local-concurrency", { method: "PUT", body }));
  concurrencySaveTail = save;
  try {
    const status = await save;
    if (!current()) return;
    renderLocalConcurrency(status);
    $("#concurrency-status").textContent =
      "Saved. New files follow these limits; running files continue.";
  } catch (error) {
    if (current()) $("#concurrency-status").textContent = error.message;
  }
}
$("#local-concurrency").addEventListener("input", () => {
  concurrencyDraftVersion++;
  $("#concurrency-status").textContent = "Changes not saved.";
});
$("#concurrency-save").addEventListener("click", () =>
  saveLocalConcurrency(false),
);
$("#concurrency-defaults").addEventListener("click", () =>
  saveLocalConcurrency(true),
);
// Enter in a limit field saves the limits rather than closing the dialog.
$("#local-concurrency").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.target.tagName !== "INPUT") return;
  event.preventDefault();
  saveLocalConcurrency(false);
});
for (const id of ["#enhance-device", "#enhance-downloads", "#enhance-python"]) {
  $(id).addEventListener("input", () => {
    processingDraftVersion++;
    $("#processing-status").textContent = "Changes not saved.";
    if (id === "#enhance-python")
      $("#processing-local-status").textContent =
        "Runtime settings changed; the previous check does not apply.";
  });
  $(id).addEventListener(
    "change",
    handle(async () => {
      const context = processingContext(),
        version = ++processingSaveVersion,
        draft = processingDraftVersion;
      const current = () =>
        version === processingSaveVersion &&
        draft === processingDraftVersion &&
        sameProcessing(context);
      const settings =
        id === "#enhance-python"
          ? { runtime_python: $("#enhance-python").value.trim() }
          : id === "#enhance-device"
            ? { device: $("#enhance-device").value }
            : { download_models: $("#enhance-downloads").checked };
      processingSettings = { ...processingSettings, ...settings };
      $("#processing-status").textContent = "Saving…";
      const save = processingSaveTail
        .catch(() => {})
        .then(() =>
          api("/enhancement-settings", { method: "PUT", body: settings }),
        );
      processingSaveTail = save;
      let saved;
      try {
        saved = await save;
      } catch (error) {
        if (current())
          $("#processing-status").textContent =
            "Save failed. Change a setting to try again.";
        throw error;
      }
      if (!current()) return;
      $("#processing-status").textContent = "Saved for new jobs.";
      $("#enhance-device").value = saved.device;
      if (id === "#enhance-python") await checkLocalRuntime();
      else {
        const checkVersion = localCheckVersion,
          checkPending = $("#enhance-check").disabled;
        const local = await api("/local-runtime", { background: true });
        if (
          current() &&
          !checkPending &&
          checkVersion === localCheckVersion &&
          !$("#enhance-check").disabled &&
          local.status
        )
          renderLocalRuntime(local.status);
      }
    }),
  );
}
$("#enhance-check").addEventListener("click", handle(checkLocalRuntime));
async function loadRuntimeCapacity() {
  const version = ++capacityViewVersion,
    input = $("#runtime-max-allocations"),
    status = $("#runtime-capacity-status");
  input.disabled = true;
  status.textContent = "Loading allocation limit…";
  await capacitySaveTail.catch(() => {});
  if (!sameCapacityView(version)) return;
  const capacity = await api("/runtime-batches/settings", {
    background: true,
  }).catch(() => null);
  if (!sameCapacityView(version)) return;
  runtimeCapacity = capacity?.max_allocations ?? null;
  input.value = runtimeCapacity ?? 2;
  input.disabled = runtimeCapacity === null;
  status.textContent =
    runtimeCapacity === null
      ? "The allocation limit could not be loaded. Reopen UCloud settings to try again."
      : "";
}
function sameCapacityView(version) {
  return (
    $("#settings-dialog").open &&
    state.settingsTab === "ucloud" &&
    capacityViewVersion === version
  );
}
$("#runtime-max-allocations").addEventListener(
  "change",
  handle(async () => {
    const input = $("#runtime-max-allocations"),
      status = $("#runtime-capacity-status");
    if (runtimeCapacity === null || !input.checkValidity()) {
      status.textContent = "Choose a whole number from 1 to 4.";
      return;
    }
    const version = ++capacitySaveVersion,
      context = capacityViewVersion,
      value = Number(input.value);
    const current = () =>
      version === capacitySaveVersion && sameCapacityView(context);
    input.disabled = true;
    status.textContent = "Saving allocation limit…";
    const save = capacitySaveTail
      .catch(() => {})
      .then(() =>
        api("/runtime-batches/settings", {
          method: "PUT",
          body: { max_allocations: value },
        }),
      );
    capacitySaveTail = save;
    try {
      const saved = await save;
      if (!current()) return;
      runtimeCapacity = saved.max_allocations;
      input.value = runtimeCapacity;
      status.textContent = "Saved. Active allocations keep running.";
    } catch (error) {
      if (!current()) return;
      input.value = runtimeCapacity;
      status.textContent = error.message;
    } finally {
      if (current()) input.disabled = runtimeCapacity === null;
    }
  }),
);
$("#runtime-max-allocations").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  event.currentTarget.blur();
});
function switchProviderTab(tab) {
  if (tab === "huggingface") return showHuggingFaceTab();
  if (tab === "processing") return showProcessingTab();
  if (!["openai", "ucloud"].includes(tab)) tab = "openai";
  showProviderTab(tab);
  const existing = state.providers.find((p) => providerTab(p) === tab);
  if (existing) fillProvider(existing);
  else newProvider();
}
document.querySelectorAll("[data-provider-tab]").forEach((button) => {
  button.addEventListener(
    "click",
    handle(() => switchProviderTab(button.dataset.providerTab)),
  );
  button.addEventListener("keydown", (event) => {
    const tabs = [...document.querySelectorAll("[data-provider-tab]")];
    const index = tabs.indexOf(button);
    const next = {
      ArrowRight: (index + 1) % tabs.length,
      ArrowLeft: (index + tabs.length - 1) % tabs.length,
      Home: 0,
      End: tabs.length - 1,
    }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    tabs[next].click();
    tabs[next].focus();
  });
});
function fillProvider(settings) {
  if (!visibleProvider(settings)) {
    switchProviderTab("openai");
    return;
  }
  showProviderTab(providerTab(settings));
  state.settingsProvider = settings.id || null;
  providerFormProfile = structuredClone(settings);
  renderProviderSelects();
  $("#provider-name").value =
    settings.name || configurationLabel(null, settings.model) || "";
  $("#base-url").value = settings.base_url;
  $("#api-key").value = "";
  $("#key-status").textContent = settings.has_key
    ? "Key available"
    : "No key saved";
  $("#model").value = settings.model;
  $("#language").value = settings.language;
  const runtime = whisperxProvider(settings.id);
  const languageHidden =
    runtime || ["ucloud", "openai"].includes(settings.provider_type);
  $("#language-label").hidden = languageHidden;
  $("#language").disabled = languageHidden;
  $("#capability").value = settings.capability;
  $("#upload-limit").value = settings.max_upload_mb;
  $("#concurrent-chunks").value = settings.concurrent_chunks || 1;
  $("#ucloud-server").value = settings.ucloud?.server || "https://cloud.sdu.dk";
  $("#ucloud-project").value = settings.ucloud?.project_id || "";
  $("#hf-model").value =
    settings.ucloud?.hf_model ||
    (settings.provider_type === "ucloud-vllm" ? settings.model : "");
  $("#hf-revision").value = settings.ucloud?.hf_revision || "main";
  if (settings.provider_type === "ucloud-vllm")
    $("#model").value = $("#hf-model").value;
  renderCatalog();
  const native = settings.ucloud_transcriber || {};
  state.nativeResolved = native;
  state.nativeChoices = {};
  $("#native-discovery-choices").replaceChildren();
  $("#native-cleanup").checked = native.cleanup_after_success ?? !settings.id;
  $("#native-model-choice").value =
    state.modelPresets.ucloud_transcriber.models.some(
      (m) => m.model === settings.model,
    )
      ? settings.model
      : "";
  for (const [id, field, fallback] of nativeFields) {
    $(`#native-${id}`).value = native[field] ?? fallback;
  }
  $("#ucloud-token-status").textContent = settings.has_ucloud_token
    ? "Token saved"
    : "Not saved";
  $("#hf-token-status").textContent = settings.has_hf_token
    ? "Token saved"
    : "Not saved";
  $("#forget-hosting-keys").disabled = !settings.id;
  $("#provider-preset").value =
    Object.keys(presets).find((key) => presets[key].model === settings.model) ||
    [...$("#provider-preset").options].find(
      (option) => option.dataset.existingModel === settings.model,
    )?.value ||
    "";
  document.querySelectorAll('[name="approved-for"]').forEach((input) => {
    input.checked = (settings.approved_for || []).includes(input.value);
  });
  capabilityHelp();
  providerSummary();
  $("#delete-provider").disabled = !settings.id;
  renderEnabledModels();
}
function newProvider() {
  const openai = state.settingsTab === "openai";
  const vllm = state.settingsTab === "ucloud-vllm";
  const native = state.settingsTab === "ucloud";
  const openaiDefault = state.modelPresets.openai.models.find(
    (m) => m.model === state.modelPresets.openai.default,
  );
  const nativeDefault = state.modelPresets.ucloud_transcriber.models.find(
    (m) => m.model === state.modelPresets.ucloud_transcriber.default,
  );
  fillProvider({
    provider_type: state.settingsTab,
    base_url: native
      ? "https://cloud.sdu.dk"
      : openai
        ? OPENAI_URL
        : vllm
          ? ""
          : "http://localhost:8000/v1",
    model: native
      ? nativeDefault.model
      : openai
        ? openaiDefault.model
        : vllm
          ? "openai/whisper-large-v3-turbo"
          : "custom-model",
    language: "",
    name: native
      ? nativeDefault.name
      : openai
        ? openaiDefault.name
        : vllm
          ? "UCloud vLLM openai/whisper-large-v3-turbo"
          : "Custom backend",
    capability: native
      ? nativeDefault.capability
      : openai
        ? openaiDefault.capability
        : vllm
          ? "segments"
          : "json",
    max_upload_mb: vllm ? 14 : 20,
    concurrent_chunks: openai ? 2 : 1,
    approved_for: state.protectedDefaults[state.settingsTab]
      ? ["protected"]
      : [],
    ucloud: vllm
      ? {
          hf_model: "openai/whisper-large-v3-turbo",
          hf_revision: state.catalog.models.find(
            (m) => m.id === "openai/whisper-large-v3-turbo",
          ).revision,
        }
      : null,
  });
  (vllm ? $("#ucloud-token") : $("#api-key")).focus();
}
async function openSettings(identifier = state.defaultProvider) {
  const version = ++providerFormVersion;
  await loadProviders();
  if (version !== providerFormVersion) return;
  const selected =
    state.providers.find((p) => p.id === identifier && visibleProvider(p)) ||
    state.providers.find(visibleProvider);
  if (selected) fillProvider(selected);
  else switchProviderTab("openai");
  $("#settings-dialog").showModal();
}
$("#settings-open").addEventListener(
  "click",
  handle(() => openSettings()),
);
// Opening another library restarts the server; unsaved drafts must be settled first.
const libraryChooser = CBSLibraries.bind($("#library-panel"), {
  beforeOpen: () => canLeave(),
});
$("#library-open").addEventListener(
  "click",
  handle(async () => {
    $("#library-dialog").showModal();
    await Promise.all([libraryChooser.load(), renderBackups()]);
  }),
);
// Complete backups, restore and UCloud storage live in portable.js (ADR 0096).
// Earlier database-only inventories (ADR 0061) stay available under their own disclosure.
async function renderBackups() {
  const listing = await api("/library/backups");
  $("#legacy-backups").hidden = !listing.backups.length;
  $("#library-backups").innerHTML = listing.backups.length
    ? listing.backups
        .slice(0, 5)
        .map(
          (b) =>
            `<li class="library-item"><div><strong>${h(new Date(b.created).toLocaleString("en-GB"))}</strong><span class="library-path">${h(b.folder)}</span><span class="library-facts">${h(b.recordings)} recording(s) · ${h(b.originals)} original(s) fingerprinted${b.missing_originals ? ` · ${h(b.missing_originals)} original(s) missing` : ""}</span></div></li>`,
        )
        .join("")
    : "";
}
$("#settings-provider").addEventListener("change", (event) => {
  const selected = state.providers.find((p) => p.id === event.target.value);
  if (selected) fillProvider(selected);
  else newProvider();
});
$("#new-provider").addEventListener("click", newProvider);
$("#delete-provider").addEventListener(
  "click",
  handle(async () => {
    if (
      !state.settingsProvider ||
      !confirm(
        "Delete this provider configuration? Saved transcript results are kept.",
      )
    )
      return;
    const removed = state.settingsProvider;
    const context = providerContext();
    await api(`/providers/${removed}`, { method: "DELETE" });
    if (state.recording?.provider_id === removed)
      state.recording.provider_id = null;
    state.recordings.forEach((r) => {
      if (r.provider_id === removed) r.provider_id = null;
    });
    await loadProviders();
    if (sameProvider(context)) switchProviderTab(context.tab);
    if (!state.recording) renderRecordings();
  }),
);
$("#settings-dialog").addEventListener("close", () => {
  providerFormVersion++;
  $("#api-key").value = "";
  $("#ucloud-token").value = "";
  $("#hf-token").value = "";
  clearModelCheck();
});
$("#base-url").addEventListener("input", () => {
  $("#key-status").textContent = "Key is specific to this endpoint";
  clearModelCheck();
  providerSummary();
  renderEnabledModels(true);
});
function renderEnabledModels(preserveDraft = false) {
  const panel = $("#provider-model-options");
  const kind = state.settingsTab;
  if (!["openai", "ucloud"].includes(kind)) return;
  const previous =
    preserveDraft && panel.dataset.dirty === "true"
      ? new Map(
          [...panel.querySelectorAll("[data-enabled-model]")].map((input) => [
            input.dataset.enabledModel,
            {
              checked: input.checked,
              touched: input.dataset.modelTouched === "true",
              versions: input.dataset.modelVersions,
            },
          ]),
        )
      : new Map();
  const endpoint = $("#base-url").value.trim().replace(/\/+$/u, "");
  const existing = state.providers.filter(
    (p) =>
      providerTab(p) === kind &&
      p.base_url === endpoint &&
      (kind !== "ucloud" ||
        p.ucloud_transcriber?.project_id === nativeSetup().project_id),
  );
  const freshChoices =
    existing.length === 0 ||
    (existing.length === 1 && existing[0].initial_model_defaults === true);
  panel.dataset.freshChoices = String(freshChoices);
  const catalog =
    kind === "openai" ? state.modelPresets.openai : { models: ucloudModels() };
  const models = [...catalog.models];
  for (const saved of existing)
    if (!models.some((model) => model.model === saved.model))
      models.push({ model: saved.model, name: saved.model });
  const renderModel = (model) => {
    const profiles = existing.filter((p) => p.model === model.model);
    const enabled = previous.has(model.model)
      ? previous.get(model.model).checked
      : profiles.length
        ? profiles.some((p) => p.enabled !== false)
        : !freshChoices || model.default_enabled !== false;
    const label = configurationLabel(model.name, model.model);
    const prior = previous.get(model.model);
    const versions = prior?.touched
      ? prior.versions
      : JSON.stringify(
          Object.fromEntries(profiles.map((p) => [p.id, p.version])),
        );
    return `<label class="checkbox-label"><input type="checkbox" data-enabled-model="${h(model.model)}" data-model-touched="${prior?.touched ? "true" : "false"}" data-model-versions="${h(versions)}" aria-label="${h(label)}" ${enabled ? "checked" : ""}>${h(label)}</label>`;
  };
  $("#provider-model-list").innerHTML =
    kind === "ucloud"
      ? ucloudGroupLabels()
          .map((label) => {
            const entries = models.filter(
              (model) => ucloudModelGroup(model) === label,
            );
            return entries.length
              ? `<div class="provider-model-group" role="group" aria-label="${label}"><h4 class="settings-subhead">${label}</h4>${entries.map(renderModel).join("")}</div>`
              : "";
          })
          .join("")
      : models.map(renderModel).join("");
  if (!preserveDraft) panel.dataset.dirty = "false";
}
$("#provider-model-options").addEventListener("change", (event) => {
  if (event.target.matches("[data-enabled-model]")) {
    event.target.dataset.modelTouched = "true";
    $("#provider-model-options").dataset.dirty = "true";
  }
});
function modelSelectionRequest() {
  if (!["openai", "ucloud"].includes(state.settingsTab)) return {};
  const models = [
    ...$("#provider-model-options").querySelectorAll("[data-enabled-model]"),
  ];
  const touched = models.filter(
    (input) => input.dataset.modelTouched === "true",
  );
  const fresh = $("#provider-model-options").dataset.freshChoices === "true";
  if (!fresh && !touched.length) return {};
  const changed = fresh ? models : touched;
  return {
    enabled_models: models
      .filter((input) => input.checked)
      .map((input) => input.dataset.enabledModel),
    enabled_models_touched: changed.map((input) => input.dataset.enabledModel),
    enabled_model_versions: Object.assign(
      Object.create(null),
      ...changed.map((input) => JSON.parse(input.dataset.modelVersions)),
    ),
  };
}
function choosePresetModel(preset) {
  const endpoint = $("#base-url").value;
  const keyDraft = $("#api-key").value;
  const current = state.providers.find((p) => p.id === state.settingsProvider);
  const existing =
    current?.model === preset.model &&
    (state.settingsTab !== "ucloud" ||
      current.ucloud_transcriber?.project_id === nativeSetup().project_id)
      ? current
      : state.providers.find(
          (p) =>
            providerTab(p) === state.settingsTab &&
            p.model === preset.model &&
            p.base_url === endpoint &&
            (state.settingsTab !== "ucloud" ||
              p.ucloud_transcriber?.project_id === nativeSetup().project_id),
        );
  if (existing && existing.id !== state.settingsProvider) {
    fillProvider(existing);
    $("#api-key").value = keyDraft; // Model changes retain a draft key only on the same endpoint.
  } else if (!existing && current) {
    state.settingsProvider = null;
    $("#settings-provider").value = "";
    $("#delete-provider").disabled = true;
    $("#provider-name").value = preset.name;
  }
  if (
    !$("#provider-name").value ||
    Object.values(presets).some((p) => p.name === $("#provider-name").value) ||
    ucloudModels().some((p) => p.name === $("#provider-name").value)
  )
    $("#provider-name").value = preset.name;
  $("#model").value = preset.model;
  $("#capability").value = preset.capability;
  if (state.settingsTab === "openai")
    $("#provider-preset").value =
      Object.keys(presets).find((key) => presets[key].model === preset.model) ||
      [...$("#provider-preset").options].find(
        (option) => option.dataset.existingModel === preset.model,
      )?.value ||
      "";
  else $("#native-model-choice").value = preset.model;
  clearModelCheck();
  capabilityHelp();
}
$("#provider-preset").addEventListener("change", (event) => {
  const existing = event.target.selectedOptions[0]?.dataset;
  const preset =
    presets[event.target.value] ||
    (existing?.existingModel
      ? {
          model: existing.existingModel,
          capability: existing.capability,
          name: existing.name,
        }
      : null);
  if (!preset) return;
  choosePresetModel(preset);
});
$("#model").addEventListener("input", () => {
  clearModelCheck();
});
$("#native-model-choice").addEventListener("change", (event) => {
  const group = state.modelPresets.ucloud_transcriber;
  const model = group.models.find((m) => m.model === event.target.value);
  if (!model) return;
  choosePresetModel(model);
  $("#model-value-label").hidden = true;
});
$("#language").addEventListener("input", clearModelCheck);
$("#api-key").addEventListener("input", clearModelCheck);
$("#ucloud-server").addEventListener("input", () => {
  $("#ucloud-token-status").textContent =
    "Token is specific to this UCloud server";
});
$("#forget-hosting-keys").addEventListener(
  "click",
  handle(async () => {
    const settings = state.providers.find(
      (p) => p.id === state.settingsProvider,
    );
    if (!settings) throw new Error("Save this configuration first.");
    const context = providerContext();
    if (
      !confirm(
        "Forget the UCloud and Hugging Face setup tokens? Configurations using this UCloud server or Hugging Face share these tokens.",
      )
    )
      return;
    await api(`/providers/${settings.id}`, {
      method: "PUT",
      body: {
        ...settings,
        expected_version: settings.version,
        ucloud_token: "",
        hf_token: "",
        remember: true,
      },
    });
    await loadProviders();
    if (sameProvider(context))
      fillProvider(state.providers.find((p) => p.id === settings.id));
    toast("Hosting tokens forgotten");
  }),
);
$("#check-models").addEventListener("click", async () => {
  const token = ++modelCheckToken;
  const button = $("#check-models");
  button.disabled = true;
  $("#model-check-results").textContent =
    "Checking models visible to this API key…";
  try {
    const result = await api("/providers/check-models", {
      method: "POST",
      body: {
        provider_type: state.settingsTab,
        base_url: $("#base-url").value,
        model: $("#model").value,
        api_key: $("#api-key").value || null,
      },
    });
    if (token !== modelCheckToken) return;
    $("#model-check-results").innerHTML =
      `<ul class="model-availability">${result.models.map((model) => `<li><span>${h(model.name)}</span><strong class="${model.listed ? "listed" : "unlisted"}">${model.listed ? "Listed" : "Not listed"}</strong></li>`).join("")}</ul><p class="settings-note">${h(result.note)}</p>`;
  } catch (error) {
    if (token === modelCheckToken)
      $("#model-check-results").textContent =
        `Could not check availability: ${error.message}`;
  } finally {
    button.disabled = false;
  }
});
function capabilityHelp() {
  providerSummary();
  if (state.settingsTab === "ucloud") {
    $("#capability-help").textContent =
      "Imports word timing and speaker labels when supplied by the Batch application. Presets depend on the selected app version; unsupported models are rejected before upload.";
    return;
  }
  $("#capability-help").textContent = {
    words:
      "Requests verbose JSON with word and segment timestamps. OpenAI supports this with whisper-1; compatible backends must support these parameters.",
    words_speakers:
      "For compatible backends returning both timed words and speaker labels in verbose JSON. Configure this only when the backend supports both.",
    speakers:
      "Requests diarized JSON with speaker labels. The OpenAI diarization preset supplies segment timestamps without word-level timing.",
    segments:
      "Requests verbose JSON without word timestamp parameters. Highlights segments when reliable segment times are supplied; missing times stay untimed.",
    json: "Accepts JSON text. If your provider omits timestamps, the transcript stays readable and editable without synchronized highlighting.",
  }[$("#capability").value];
  if (state.settingsTab === "openai" && $("#capability").value === "json")
    $("#capability-help").textContent =
      "This model returns transcript text without speaker labels or timestamps. You can read, edit and export it; synchronized highlighting needs timestamped output.";
  if (state.settingsTab === "ucloud-vllm")
    $("#capability-help").textContent +=
      " The hosted model must support audio transcription and this response format. A chat or PII model alone cannot transcribe audio.";
}
$("#capability").addEventListener("change", capabilityHelp);
$("#settings-form").addEventListener(
  "submit",
  handle(async (event) => {
    event.preventDefault();
    const button = event.submitter;
    const context = providerContext();
    button.disabled = true;
    const key = $("#api-key").value;
    try {
      if (state.settingsTab === "ucloud" && !nativeReady(nativeSetup())) {
        if (!(await connectNative())) return;
      }
      if (!sameProvider(context)) return;
      await api(
        state.settingsProvider
          ? `/providers/${state.settingsProvider}`
          : "/providers",
        {
          method: state.settingsProvider ? "PUT" : "POST",
          body: {
            // Reject the save if another window changed this provider meanwhile.
            expected_version:
              providerFormProfile?.id === state.settingsProvider
                ? providerFormProfile.version
                : undefined,
            provider_type: state.settingsTab,
            ...modelSelectionRequest(),
            name: $("#provider-name").value,
            base_url: $("#base-url").value,
            model: $("#model").value,
            ...(["openai", "ucloud"].includes(state.settingsTab)
              ? {}
              : { language: $("#language").value }),
            capability: $("#capability").value,
            max_upload_mb: Number($("#upload-limit").value),
            concurrent_chunks: Number($("#concurrent-chunks").value),
            ucloud_transcriber:
              state.settingsTab === "ucloud" ? nativeSetup() : null,
            native_allocation_choice:
              state.settingsTab === "ucloud" &&
              !state.settingsProvider &&
              !nativeSetup().template_job_id
                ? "mig1g"
                : undefined,
            ucloud:
              state.settingsTab === "ucloud-vllm"
                ? {
                    server: $("#ucloud-server").value,
                    project_id: $("#ucloud-project").value,
                    hf_model: $("#hf-model").value,
                    hf_revision: $("#hf-revision").value,
                  }
                : null,
            ucloud_token:
              state.settingsTab === "ucloud-vllm"
                ? $("#ucloud-token").value || null
                : null,
            hf_token:
              state.settingsTab === "ucloud-vllm"
                ? $("#hf-token").value || null
                : null,
            approved_for: [
              ...document.querySelectorAll('[name="approved-for"]:checked'),
            ].map((input) => input.value),
            api_key: key || null,
            remember: $("#remember-key").checked,
          },
        },
      );
      await loadProviders();
      if (sameProvider(context)) $("#settings-dialog").close();
      if (state.recording) renderEditorDetails();
      else renderRecordings();
      toast("Settings saved");
    } finally {
      button.disabled = false;
    }
  }),
);
$("#forget-key").addEventListener(
  "click",
  handle(async () => {
    if (
      !confirm(
        "Forget this endpoint's saved key? Other configurations using the same endpoint share it. Environment variables are unaffected.",
      )
    )
      return;
    const settings = state.providers.find(
      (p) => p.id === state.settingsProvider,
    );
    if (!settings) throw new Error("Save the provider configuration first.");
    const context = providerContext();
    await api(`/providers/${settings.id}`, {
      method: "PUT",
      body: {
        ...settings,
        expected_version: settings.version,
        api_key: "",
        remember: true,
      },
    });
    await loadProviders();
    if (sameProvider(context)) {
      $("#api-key").value = "";
      $("#key-status").textContent = "Key forgotten for this endpoint";
    }
    toast("Saved endpoint key forgotten");
  }),
);

$("#activity-open").addEventListener("click", () => {
  $("#activity").hidden = !$("#activity").hidden;
});
$("#activity-close").addEventListener("click", () => {
  $("#activity").hidden = true;
});
const pendingRecordingNotes = new Map();
const cardNoteDrafts = new Map();
function rememberCardNotes(host) {
  const input = host.querySelector("textarea");
  const previous = cardNoteDrafts.get(host.dataset.cardNotes);
  // A completed save can arrive while its old card is hidden behind recording review.
  if (previous && previous.revision > Number(input.dataset.revision)) return;
  cardNoteDrafts.set(host.dataset.cardNotes, {
    text: notesValue(input),
    baseline: input.notesBaseline ?? input.defaultValue,
    revision: Number(input.dataset.revision),
    open: host.open,
    status: host.querySelector('[role="status"]').textContent,
  });
}
function cardNotesMarkup(recording) {
  const draft = cardNoteDrafts.get(recording.id);
  const pending = pendingRecordingNotes.get(recording.id);
  const dirty = draft && draft.text !== draft.baseline;
  const baseline =
    pending?.baseline ?? (dirty ? draft.baseline : recording.notes || "");
  const revision =
    pending?.revision ??
    (dirty ? draft.revision : recording.notes_revision || 0);
  return `<details class="description-notes" data-card-notes="${recording.id}" ${draft?.open ? "open" : ""}><summary>Description / notes</summary><textarea data-recording="${recording.id}" data-revision="${revision}" maxlength="20000" aria-label="Description / notes for ${h(recording.name)}" ${pending ? "disabled" : ""}>${h(baseline)}</textarea><button class="button" data-save-card-notes ${pending ? "disabled" : ""}>Save notes</button><span role="status">${h(pending ? "Saving notes…" : dirty ? draft.status || "Unsaved notes" : draft?.status || "")}</span></details>`;
}
function restoreCardNoteDrafts() {
  document.querySelectorAll("[data-card-notes]").forEach((host) => {
    const draft = cardNoteDrafts.get(host.dataset.cardNotes);
    const pending = pendingRecordingNotes.get(host.dataset.cardNotes);
    const recording = state.recordings.find(
      (r) => r.id === host.dataset.cardNotes,
    );
    const retained =
      pending || (draft?.text !== draft?.baseline ? draft : null);
    const input = host.querySelector("textarea");
    input.notesBaseline = retained?.baseline ?? recording?.notes ?? "";
    input.defaultValue = input.notesBaseline;
    input.value = retained?.text ?? input.notesBaseline;
  });
}
$("#recordings").addEventListener(
  "toggle",
  (event) => {
    if (event.target.matches("[data-card-notes]"))
      rememberCardNotes(event.target);
  },
  true,
);
$("#recordings").addEventListener("input", (event) => {
  const host = event.target.closest("[data-card-notes]");
  if (!host) return;
  host.querySelector('[role="status"]').textContent =
    notesValue(event.target) !==
    (event.target.notesBaseline ?? event.target.defaultValue)
      ? "Unsaved notes"
      : "";
  rememberCardNotes(host);
});
$("#recordings").addEventListener(
  "click",
  handle(async (event) => {
    const button = event.target.closest("[data-save-card-notes]");
    if (!button) return;
    const host = button.closest("[data-card-notes]");
    await saveRecordingNotes(
      host.querySelector("textarea"),
      button,
      host.querySelector('[role="status"]'),
    );
  }),
);
function recordingNotesDirty() {
  const input = $("#recording-notes");
  if (state.recording)
    return (
      !pendingRecordingNotes.has(state.recording.id) &&
      input?.dataset.recording === state.recording.id &&
      notesValue(input) !== (input.notesBaseline ?? input.defaultValue)
    );
  return state.recordings.some((recording) => {
    const draft = cardNoteDrafts.get(recording.id);
    return (
      draft &&
      !pendingRecordingNotes.has(recording.id) &&
      draft.text !== draft.baseline
    );
  });
}
function renderRecordingNotes() {
  const input = $("#recording-notes"),
    recording = state.recording;
  if (!input || !recording || recordingNotesDirty()) return;
  const pending = pendingRecordingNotes.get(recording.id);
  const draft = cardNoteDrafts.get(recording.id);
  const retained = pending || (draft?.text !== draft?.baseline ? draft : null);
  input.dataset.recording = recording.id;
  input.dataset.revision = retained?.revision ?? recording.notes_revision ?? 0;
  input.notesBaseline = retained?.baseline ?? recording.notes ?? "";
  input.defaultValue = input.notesBaseline;
  input.value = retained?.text ?? input.notesBaseline;
  input.disabled = $("#recording-notes-save").disabled = !!pending;
  $("#recording-notes-status").textContent = pending
    ? "Saving notes…"
    : retained?.status || "";
}
$("#recording-notes").addEventListener("input", () => {
  $("#recording-notes-status").textContent = recordingNotesDirty()
    ? "Unsaved notes"
    : "";
});
async function saveRecordingNotes(input, button, status) {
  const id = input.dataset.recording;
  if (pendingRecordingNotes.has(id)) return;
  const text = notesValue(input),
    revision = Number(input.dataset.revision);
  const frozen = {
    text,
    revision,
    baseline: input.notesBaseline ?? input.defaultValue,
  };
  pendingRecordingNotes.set(id, frozen);
  cardNoteDrafts.set(id, {
    ...cardNoteDrafts.get(id),
    ...frozen,
    status: "Saving notes…",
  });
  button.disabled = input.disabled = true;
  status.textContent = "Saving notes…";
  const host = input.closest("[data-card-notes]");
  if (host) rememberCardNotes(host);
  let result;
  try {
    result = await api(`/recordings/${id}/notes`, {
      method: "PATCH",
      body: { text, revision },
    });
    // A later read may have observed another window's newer save while this reply waited.
    for (const recording of [state.recording, ...state.recordings]) {
      if (
        recording?.id === id &&
        (recording.notes_revision || 0) > result.notes_revision
      )
        result = {
          notes: recording.notes,
          notes_revision: recording.notes_revision,
        };
    }
    for (const recording of [state.recording, ...state.recordings]) {
      if (
        recording?.id === id &&
        (recording.notes_revision || 0) <= result.notes_revision
      ) {
        recording.notes = result.notes;
        recording.notes_revision = result.notes_revision;
      }
    }
    cardNoteDrafts.set(id, {
      text: result.notes,
      baseline: result.notes,
      revision: result.notes_revision,
      open: cardNoteDrafts.get(id)?.open,
      status: "Notes saved",
    });
    if (input.isConnected && input.dataset.recording === id) {
      input.notesBaseline = result.notes;
      input.value = input.defaultValue = result.notes;
      input.dataset.revision = result.notes_revision;
      status.textContent = "Notes saved";
    }
  } catch (error) {
    if (input.isConnected && input.dataset.recording === id)
      status.textContent = "Save failed — notes retained";
    cardNoteDrafts.set(id, {
      ...cardNoteDrafts.get(id),
      ...frozen,
      status: "Save failed — notes retained",
    });
    throw error;
  } finally {
    pendingRecordingNotes.delete(id);
    if (input.isConnected && input.dataset.recording === id)
      button.disabled = input.disabled = false;
    const current = $("#recording-notes");
    if (state.recording?.id === id && current.dataset.recording === id) {
      if (result) {
        current.notesBaseline = result.notes;
        current.value = current.defaultValue = result.notes;
        current.dataset.revision = result.notes_revision;
      }
      current.disabled = $("#recording-notes-save").disabled = false;
      $("#recording-notes-status").textContent = result
        ? "Notes saved"
        : "Save failed — notes retained";
    }
    if (
      !state.recording &&
      state.recordings.some((recording) => recording.id === id)
    )
      renderRecordings(false);
  }
}
$("#recording-notes-save").addEventListener(
  "click",
  handle((event) =>
    saveRecordingNotes(
      $("#recording-notes"),
      event.target,
      $("#recording-notes-status"),
    ),
  ),
);

let enhancementContext = null;
function renderEnhancementExecution() {
  const batch = !!enhancementContext?.transcription;
  const passages = enhancementContext?.retranscribe;
  // Re-transcription replaces wording only: word timing follows, speakers stay as they are.
  $("#enhance-whisper-label").hidden = !passages;
  $("#retranscribe-summary").hidden = !passages;
  $("#retranscribe-summary").textContent = passages
    ? `${passages.ids.length} passage(s) · new wording from WhisperX for these passages only; all other passages are copied unchanged into the new result.`
    : "";
  $("#enhance-features").hidden = !!passages;
  const only = batch && $("#batch-mode").value === "transcribe";
  $("#enhancement-options").hidden = only;
  $("#enhancement-options").disabled = only;
  const mode = $("#enhance-execution").value;
  const cloud = mode === "ucloud",
    auto = mode === "auto";
  $("#enhance-start").textContent = passages
    ? `Re-transcribe ${passages.ids.length} passage(s)`
    : batch
      ? enhancementContext.transcription.ids.length === 1
        ? "Start transcription"
        : "Start batch"
      : cloud
        ? "Add features on UCloud"
        : auto
          ? "Add features"
          : "Add features locally";
}
$("#enhance-execution").addEventListener("change", renderEnhancementExecution);
$("#batch-mode").addEventListener("change", renderEnhancementExecution);
// Researcher-set Automatic rules (T68): shown as a sentence and editable below it.
function renderRoutingRules(status) {
  const limit = status.local_minutes;
  $("#enhance-auto-note").textContent =
    `Automatic: recordings up to ${limit.words} minutes (${limit.speakers} with speaker labels) run on this computer; ` +
    `a local CUDA GPU allows ${status.cuda_factor}× longer. Longer recordings run on UCloud through your connection ` +
    "when it is allowed for the project and its runtime is prepared. " +
    (status.fallback === "stop"
      ? "Otherwise they are not started. "
      : "Otherwise they run here, more slowly. ") +
    "Each recording's choice and reason appear in Activity.";
  $("#rule-word-minutes").value = status.rules.word_minutes;
  $("#rule-speaker-minutes").value = status.rules.speaker_minutes;
  $("#rule-cuda-factor").value = status.rules.cuda_factor;
  $("#rule-fallback").value = status.rules.fallback;
  $("#rule-defaults").dataset.defaults = JSON.stringify(status.defaults);
}
let routingDraftVersion = 0,
  routingSaveVersion = 0,
  routingSaveTail = Promise.resolve();
async function saveRoutingRules(values) {
  const version = ++routingSaveVersion,
    context = processingContext(),
    draft = routingDraftVersion;
  const current = () =>
    version === routingSaveVersion &&
    draft === routingDraftVersion &&
    sameProcessing(context);
  cloudStatusVersion++;
  $("#rule-status").textContent = "Saving…";
  const frozen = { ...values };
  const save = routingSaveTail
    .catch(() => {})
    .then(() => api("/enhancement-routing", { method: "PUT", body: frozen }));
  routingSaveTail = save;
  try {
    const status = await save;
    if (!current()) return;
    renderRoutingRules(status);
    $("#rule-status").textContent = "Rules saved for new jobs in this library.";
  } catch (error) {
    if (current()) $("#rule-status").textContent = error.message;
  }
}
$("#enhance-rules").addEventListener("input", () => {
  routingDraftVersion++;
  $("#rule-status").textContent = "Changes not saved.";
});
function routingRuleValues() {
  return {
    word_minutes: Number($("#rule-word-minutes").value),
    speaker_minutes: Number($("#rule-speaker-minutes").value),
    cuda_factor: Number($("#rule-cuda-factor").value),
    fallback: $("#rule-fallback").value,
  };
}
$("#rule-save").addEventListener("click", () =>
  saveRoutingRules(routingRuleValues()),
);
$("#rule-defaults").addEventListener("click", (event) =>
  saveRoutingRules(JSON.parse(event.currentTarget.dataset.defaults || "{}")),
);
// Enter in a rule field saves the rules instead of starting the enhancement.
$("#enhance-rules").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.target.tagName !== "INPUT") return;
  event.preventDefault();
  saveRoutingRules(routingRuleValues());
});
let cloudStatusVersion = 0,
  cloudCheckVersion = 0,
  cloudViewVersion = 0,
  cloudSaveVersion = 0,
  cloudConnectionFingerprint = null;
function cloudContext() {
  return { version: cloudViewVersion, id: state.settingsProvider };
}
function sameCloud(context) {
  return (
    $("#settings-dialog").open &&
    state.settingsTab === "ucloud" &&
    context.version === cloudViewVersion &&
    context.id === state.settingsProvider
  );
}
async function showCloudRuntime() {
  cloudViewVersion++;
  const context = cloudContext(),
    current = () => sameCloud(context);
  $("#enhance-prepare-ucloud").disabled = true;
  $("#enhance-cloud-check").disabled = true;
  $("#enhance-cloud-save-status").textContent = "";
  try {
    await processingSaveTail.catch(() => {});
    if (!current()) return;
    const settings = await api("/enhancement-settings", { background: true });
    if (!current()) return;
    $("#enhance-prepare-ucloud").checked =
      settings.prepare_ucloud_models === true;
    $("#enhance-prepare-ucloud").disabled = false;
    const status = await renderCloudEnhancementStatus();
    if (current() && status?.connected) await checkCloudRuntime();
  } catch (error) {
    if (current())
      $("#enhance-cloud-check-result").textContent =
        `${error.message} Reopen the UCloud tab to retry.`;
  }
}
$("#enhance-prepare-ucloud").addEventListener("change", () => {
  const version = ++cloudSaveVersion,
    context = cloudContext(),
    enabled = $("#enhance-prepare-ucloud").checked,
    current = () => version === cloudSaveVersion && sameCloud(context);
  $("#enhance-cloud-save-status").textContent = "Saving…";
  const save = processingSaveTail
    .catch(() => {})
    .then(() =>
      api("/enhancement-settings", {
        method: "PUT",
        body: { prepare_ucloud_models: enabled },
      }),
    );
  processingSaveTail = save;
  save.then(
    () => {
      if (current())
        $("#enhance-cloud-save-status").textContent = "Saved for new jobs.";
    },
    () => {
      if (current())
        $("#enhance-cloud-save-status").textContent =
          "Save failed. Change the setting to try again.";
    },
  );
});
async function renderCloudEnhancementStatus() {
  const version = ++cloudStatusVersion,
    context = providerContext(),
    cloud = context.tab === "ucloud" ? cloudContext() : null;
  cloudCheckVersion++;
  cloudConnectionFingerprint = null;
  $("#enhance-cloud-activity").hidden = true;
  $("#enhance-cloud-check-result").textContent = "";
  const status = await api("/enhancement-api/status");
  if (
    version !== cloudStatusVersion ||
    (cloud ? !sameCloud(cloud) : !sameProvider(context))
  )
    return;
  if (state.settingsTab === "processing") renderRoutingRules(status);
  cloudConnectionFingerprint = status.connection_fingerprint || null;
  $("#enhance-cloud-connection").textContent = status.connected
    ? `Saved CBS Runtime connection: ${status.provider_name} · project ${status.project_id} · storage ${status.storage_path}. Runtime ${status.runtime_id}.`
    : status.message;
  $("#enhance-cloud-check").disabled = !status.connected;
  return status;
}
const BASE_RUNTIME_READY =
  "The base CBS Runtime on UCloud is prepared for this app version. Model readiness is checked separately when work is requested.";
async function checkCloudRuntime({ manual = false } = {}) {
  const button = $("#enhance-cloud-check"),
    version = ++cloudCheckVersion,
    context = cloudContext(),
    fingerprint = cloudConnectionFingerprint;
  const current = () => version === cloudCheckVersion && sameCloud(context);
  button.disabled = true;
  $("#enhance-cloud-activity").hidden = true;
  $("#enhance-cloud-check-result").textContent =
    "Checking your UCloud storage (read only)…";
  let requestingPreparation = false;
  try {
    if (manual) {
      await processingSaveTail;
      if (!current()) return;
    }
    // Opening Settings and every readiness probe remain read-only.
    const checked = await api("/enhancement-api/check", {
      method: "POST",
      body: {},
      background: true,
    });
    if (!current()) return;
    if (checked.prepared === true) {
      $("#enhance-cloud-check-result").textContent = BASE_RUNTIME_READY;
      return;
    }
    if (checked.prepared !== false) {
      $("#enhance-cloud-check-result").textContent =
        "UCloud runtime readiness could not be established. Check again before preparing.";
      return;
    }
    $("#enhance-cloud-check-result").textContent =
      "The base CBS Runtime on UCloud is not prepared yet.";
    if (!manual) return;
    // The live checkbox is not a saved permission receipt. Recheck the saved flag
    // after all accepted writes, and refuse a choice changed during this read.
    const savedWrites = processingSaveTail;
    await savedWrites;
    if (!current()) return;
    const saved = await api("/enhancement-settings", { background: true });
    if (!current()) return;
    if (
      savedWrites !== processingSaveTail ||
      !$("#enhance-prepare-ucloud").checked ||
      saved.prepare_ucloud_models !== true
    ) {
      $("#enhance-cloud-check-result").textContent +=
        " Enable and save automatic preparation, then check again to prepare it.";
      return;
    }
    if (!fingerprint)
      throw new Error(
        "Reopen UCloud settings to load the current connection before preparing it.",
      );
    $("#enhance-cloud-check-result").textContent =
      "Requesting base runtime preparation…";
    requestingPreparation = true;
    const result = await api("/enhancement-api/prepare", {
      method: "POST",
      body: { connection_fingerprint: fingerprint },
      background: true,
    });
    requestingPreparation = false;
    if (!result.operation) {
      if (result.prepared !== true)
        throw new Error("UCloud runtime preparation was not accepted.");
      if (current())
        $("#enhance-cloud-check-result").textContent = BASE_RUNTIME_READY;
      return;
    }
    const identifier = runtimePreparationId(result.operation);
    if (!identifier)
      throw new Error(
        "The preparation receipt could not be identified. Check Activity before retrying.",
      );
    // An accepted owner remains real even if its initiating dialog has closed.
    try {
      await pollJobs();
    } catch {
      toast(
        "Runtime preparation was accepted. Activity could not refresh; check it before retrying.",
        true,
      );
    }
    if (current()) {
      $("#enhance-cloud-check-result").textContent = result.deduplicated
        ? "Runtime preparation is already in Activity."
        : "Runtime preparation queued. Follow its progress in Activity.";
      $("#enhance-cloud-activity").dataset.runtimePreparationId = identifier;
      $("#enhance-cloud-activity").hidden = false;
    }
  } catch (error) {
    const uncertain =
      requestingPreparation && (!error.status || error.status >= 500);
    if (uncertain) {
      try {
        await pollJobs();
      } catch {
        // Keep the original request failure; a later poll can recover its owner.
      }
    }
    if (current())
      $("#enhance-cloud-check-result").textContent =
        error.message +
        (uncertain
          ? " Check Activity before requesting preparation again."
          : "");
  } finally {
    if (version === cloudCheckVersion && sameCloud(context))
      button.disabled = false;
  }
}
$("#enhance-cloud-check").addEventListener("click", () =>
  checkCloudRuntime({ manual: true }),
);
$("#enhance-cloud-activity").addEventListener("click", () => {
  $("#settings-dialog").close();
  $("#activity").hidden = false;
  $("#activity-close").focus();
});
async function openEnhancementDialog(context) {
  enhancementContext = context;
  const batch = context.transcription;
  $("#batch-settings").hidden = !batch;
  $("#batch-mode").value = "enhance";
  $("#enhancement-title").textContent = context.retranscribe
    ? "Re-transcribe passages"
    : batch
      ? batch.ids.length === 1
        ? "Transcription settings"
        : "Batch transcription"
      : "Add word timing / speakers";
  $("#batch-summary").textContent = batch
    ? `${batch.ids.length} recordings · ${configurationLabel(state.providers.find((p) => p.id === batch.providerIds[0])?.name, state.providers.find((p) => p.id === batch.providerIds[0])?.model)}`
    : "";
  if (batch) {
    const native = nativeFeatures(batch.providerIds[0]);
    $("#enhance-align").checked = batch.requestedFeatures?.word_timing ?? true;
    $("#enhance-diarize").checked =
      batch.requestedFeatures?.speaker_labels ?? true;
    $("#enhance-align").disabled = native.word_timing;
    $("#enhance-diarize").disabled = native.speaker_labels;
  } else {
    $("#enhance-align").disabled = false;
    $("#enhance-diarize").disabled = false;
  }
  const settings = await api("/enhancement-settings");
  // Without UCloud processing the only place offered is this computer (ADR 0145).
  $("#enhance-execution").value = processingPlace();
  renderEnhancementExecution();
  // Machine settings live in Settings → Processing (ADR 0059).
  $("#enhance-summary").textContent =
    `${settings.language === "da" ? "Danish" : "English"} · ${{ auto: "automatic device (GPU when available)", cpu: "CPU", cuda: "CUDA GPU" }[settings.device]}${settings.download_models ? " · missing models download when needed" : ""}.`;
  $("#enhancement-dialog").showModal();
}
$("#enhance-processing-settings").addEventListener(
  "click",
  handle(async () => {
    $("#enhancement-dialog").close();
    await openSettings();
    await switchProviderTab("processing");
  }),
);
$("#enhancement-dialog").addEventListener("close", () => {
  cloudStatusVersion++;
});
$("#enhancement-form").addEventListener(
  "submit",
  handle(async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    try {
      if (
        enhancementContext.transcription &&
        $("#batch-mode").value === "transcribe"
      ) {
        const plan = enhancementContext.transcription;
        if (await enqueue(plan.ids, plan.providerIds))
          $("#enhancement-dialog").close();
        return;
      }
      const settings = {
        ...(await processingDefaults()),
        execution: $("#enhance-execution").value,
      };
      const options = {
        ...settings,
        allow_downloads:
          settings.allow_downloads && settings.execution !== "ucloud",
        align: $("#enhance-align").checked,
        diarize: $("#enhance-diarize").checked,
        min_score: Number($("#enhance-score").value),
        min_speakers: $("#enhance-min-speakers").value
          ? Number($("#enhance-min-speakers").value)
          : null,
        max_speakers: $("#enhance-max-speakers").value
          ? Number($("#enhance-max-speakers").value)
          : null,
        timeout_minutes: Number($("#enhance-timeout").value),
      };
      if (enhancementContext.retranscribe) {
        const plan = enhancementContext.retranscribe;
        await withPreparation(
          `/recordings/${enhancementContext.recording}/retranscriptions`,
          {
            ...options,
            align: true,
            diarize: false,
            whisper_model: $("#enhance-whisper-model").value,
            result_id: enhancementContext.result,
            revision: enhancementContext.revision,
            segment_ids: plan.ids,
          },
        );
        $("#enhancement-dialog").close();
        await pollJobs();
        toast(
          "Re-transcription queued. The new wording is saved as a separate result to compare; this result is kept.",
        );
        return;
      }
      if (enhancementContext.transcription) {
        const plan = enhancementContext.transcription;
        if (
          await enqueue(
            plan.ids,
            plan.providerIds,
            false,
            {
              word_timing: options.align,
              speaker_labels: options.diarize,
            },
            options,
          )
        )
          $("#enhancement-dialog").close();
        return;
      }
      await withPreparation(
        `/recordings/${enhancementContext.recording}/enhancements`,
        {
          ...options,
          result_id: enhancementContext.result,
          revision: enhancementContext.revision,
        },
      );
      $("#enhancement-dialog").close();
      await pollJobs();
      $("#activity").hidden = false;
      toast(
        `${settings.execution === "ucloud" ? "UCloud" : "Local"} enhancement queued. The reviewed result is kept.`,
      );
    } finally {
      button.disabled = false;
    }
  }),
);
const pendingInputActions = new Set();
function inputPreparationToken(job) {
  const input = job.input_preparation;
  if (
    !input ||
    !/^[0-9a-f]{32}$/.test(job.id) ||
    !Number.isSafeInteger(input.epoch) ||
    input.epoch < 1
  )
    return null;
  if (
    input.kind === "intent" &&
    ["waiting", "materializing", "terminal"].includes(input.phase)
  )
    return {
      kind: input.kind,
      job: job.id,
      epoch: input.epoch,
      phase: input.phase,
    };
  if (input.kind === "inspection" && /^[0-9a-f]{32}$/.test(input.generation))
    return {
      kind: input.kind,
      job: job.id,
      epoch: input.epoch,
      generation: input.generation,
    };
  return null;
}
function inputPreparationConsent(input) {
  const preview = input.prepare_preview;
  if (
    input.kind !== "intent" ||
    input.phase !== "terminal" ||
    input.error_code !== "needs_confirmation" ||
    !preview ||
    !["recording", "request_selection", "native_selection"].includes(
      preview.scope,
    ) ||
    !Number.isSafeInteger(preview.count) ||
    preview.count < 1 ||
    preview.count > 100 ||
    !/^[0-9a-f]{64}$/.test(preview.fingerprint) ||
    !Array.isArray(preview.recordings) ||
    !preview.recordings.length ||
    preview.recordings.length > preview.count ||
    preview.recordings.some(
      (row) =>
        !row ||
        typeof row.name !== "string" ||
        typeof row.provider_name !== "string",
    )
  )
    return null;
  return {
    fingerprint: preview.fingerprint,
    count: preview.count,
    choices: preview.recordings
      .slice(0, 8)
      .map((row) => `${row.name} · ${row.provider_name}`),
  };
}
function inputPreparationContinuation(input) {
  const continuation = input.continuation;
  return input.kind === "intent" &&
    input.phase === "terminal" &&
    input.error_code === "needs_confirmation" &&
    continuation &&
    /^[0-9a-f]{32}$/.test(continuation.request_id) &&
    Array.isArray(continuation.job_ids) &&
    continuation.job_ids.length > 0 &&
    continuation.job_ids.length <= 100 &&
    new Set(continuation.job_ids).size === continuation.job_ids.length &&
    continuation.job_ids.every((id) => /^[0-9a-f]{32}$/.test(id))
    ? continuation
    : null;
}
function inputPreparationActionKey(token) {
  // Names are display metadata; only the frozen execution authority owns the
  // pending control, including through a poll that refreshes those names.
  return JSON.stringify([
    token.kind,
    token.job,
    token.epoch,
    token.phase,
    token.generation,
    token.action,
    token.preparation?.fingerprint,
  ]);
}
function inputPreparationActions(
  job,
  allowed = ["cancel", "retry", "prepare"],
) {
  const input = job.input_preparation,
    token = inputPreparationToken(job);
  if (!token || input.error_code === "invalid_evidence")
    return '<span class="field-note">Preparation evidence could not be verified. Preserve the library; no replacement work can start from this activity.</span>';
  if (inputPreparationContinuation(input)) return "";
  const preparation =
    input.can_prepare === true ? inputPreparationConsent(input) : null;
  if (input.can_prepare === true && !preparation && allowed.includes("prepare"))
    return '<span class="field-note">Preparation preview could not be verified. Refresh Activity before continuing.</span>';
  return allowed
    .filter(
      (action) =>
        input[`can_${action}`] === true &&
        (action !== "prepare" || preparation),
    )
    .map((action) => {
      const captured = {
          ...token,
          action,
          ...(action === "prepare" ? { preparation } : {}),
        },
        frozen = JSON.stringify(captured);
      const label =
        action === "prepare"
          ? "Prepare and continue"
          : `${action === "retry" ? "Retry" : "Cancel"} ${input.kind === "inspection" ? "inspection" : "source preparation"}`;
      return `<button class="button" data-input-action="${action}" data-input-token="${h(frozen)}" ${pendingInputActions.has(inputPreparationActionKey(captured)) ? "disabled" : ""}>${label}</button>`;
    })
    .join("");
}
function inputPreparationCard(job) {
  const input = job.input_preparation,
    inspection = input.kind === "inspection",
    labels = inspection
      ? {
          pending: "Waiting to inspect the original…",
          running: "Inspecting the original audio…",
          ready: "Original inspection complete.",
          error: "Original inspection needs attention.",
        }
      : {
          waiting: "Waiting for original inspection…",
          materializing: "Preparing the transcription request…",
          terminal: "Source preparation stopped.",
        },
    invalid =
      input.error_code === "invalid_evidence" || !inputPreparationToken(job),
    continuation = inputPreparationContinuation(input),
    message = invalid
      ? "Preparation evidence could not be verified."
      : continuation
        ? `Preparation confirmation recorded; ${continuation.job_ids.length} new request(s) accepted. Their progress appears in Activity.`
        : input.error_code === "needs_confirmation"
          ? "Runtime preparation needs your confirmation."
          : labels[input.phase] || "Source preparation needs attention.",
    group =
      !inspection &&
      input.native_group_size > 1 &&
      ["waiting", "materializing"].includes(input.phase)
        ? `<p class="field-note">${h(input.native_group_waiting)} of ${h(input.native_group_size)} originals are still being prepared before this selection can start together.</p>`
        : "";
  const provider =
    !inspection && (job.provider_name || job.model)
      ? `<p class="field-note">For ${h(configurationLabel(job.provider_name, job.model) || job.model)}</p>`
      : "";
  return `<article class="job" data-input-preparation="${h(input.kind)}">${activityCardHead(job, inspection ? "Original inspection" : "Source preparation")}<h3>${h(job.recording_name)}</h3>${provider}<p>${h(message)}</p>${job.inspection_stage ? `<p>${h(job.inspection_stage)}</p>` : ""}${group}${!invalid && !continuation && job.message ? `<p class="field-note">${h(job.message)}</p>` : ""}${activityRemovalNote(job)}<div class="job-footer"><span class="badge ${["pending", "running", "waiting", "materializing"].includes(input.phase) ? "working" : invalid || input.phase === "error" ? "failed" : ""}">${h(input.phase)}</span>${jobActions(job)}</div></article>`;
}
// One Activity card per multi-file request (shared request_id), like one card
// per shared allocation. Each file keeps its own frozen token for per-file
// Retry/Cancel inside the list; nothing is merged on the server.
const INPUT_OUTCOMES = {
  waiting: "Waiting",
  inspecting: "Inspecting",
  preparing: "Preparing",
  ready: "Ready",
  needs_confirmation: "Needs confirmation",
  continued: "Continued",
  failed: "Failed",
  cancelled: "Cancelled",
};
// Files added in one upload share an upload batch and likewise render as one
// Media preparation card; each member stays an ordinary media job (V162).
function activityGroupKey(job) {
  const input = job.input_preparation;
  if (
    !job.restored_history &&
    !job.ucloud_batch_id &&
    !job.runtime_batch_id &&
    !job.runtime_batch &&
    input?.error_code !== "invalid_evidence" &&
    /^[0-9a-f]{32}$/.test(job.activity_request_id) &&
    /^[0-9a-f]{32}$/.test(job.project_id)
  )
    return `request-${job.project_id}-${job.activity_request_id}`;
  if (input) {
    if (
      input.kind === "inspection" &&
      input.error_code !== "invalid_evidence" &&
      inputPreparationToken(job) &&
      !job.restored_history &&
      /^[0-9a-f]{32}$/.test(job.inspection_upload_batch) &&
      /^[0-9a-f]{32}$/.test(job.project_id)
    )
      return `inspection-${job.project_id}-${job.inspection_upload_batch}`;
    return input.kind === "intent" &&
      input.error_code !== "invalid_evidence" &&
      inputPreparationToken(job) &&
      /^[0-9a-f]{32}$/.test(input.request_id)
      ? `intent-${input.request_id}`
      : null;
  }
  return job.kind === "media" &&
    !job.restored_history &&
    /^[0-9a-f]{32}$/.test(job.upload_batch)
    ? `upload-${job.project_id || "legacy"}-${job.upload_batch}`
    : null;
}
function inspectionStage(job) {
  const input = job?.input_preparation;
  if (!input || input.kind !== "inspection") return "";
  return (
    {
      pending: "Original inspection: waiting.",
      running: "Original inspection: running.",
      ready: "Original inspection: complete.",
    }[input.phase] || ""
  );
}
function attachBatchInspections(jobs) {
  const inspectors = new Map(
      jobs
        .filter((job) => job.kind === "source-inspect")
        .map((job) => [job.id, job]),
    ),
    owners = new Set(
      jobs.flatMap(
        (job) =>
          job.runtime_batch?.members?.map((member) => member.job_id) || [],
      ),
    ),
    candidates = new Map();
  for (const job of jobs) {
    const link = job.activity_inspection,
      inspection = inspectors.get(link?.job_id);
    if (
      !inspection ||
      link.shared !== false ||
      !inspectionStage(inspection) ||
      !inputPreparationToken(inspection) ||
      inspection.restored_history ||
      inspection.inspection_upload_batch ||
      job.restored_history ||
      job.activity_membership_complete === false ||
      job.project_id !== inspection.project_id ||
      job.recording_id !== inspection.recording_id ||
      link.generation !== inspection.input_preparation.generation ||
      !/^[0-9a-f]{32}$/.test(link.request_id) ||
      (job.runtime_batch_id && !owners.has(job.id))
    )
      continue;
    const list = candidates.get(inspection.id) || [];
    list.push(job);
    candidates.set(inspection.id, list);
  }
  const attached = new Map();
  for (const [id, consumers] of candidates) {
    const inspection = inspectors.get(id),
      requests = new Set(
        consumers.map((job) => job.activity_inspection.request_id),
      );
    if (
      requests.size !== 1 ||
      (["pending", "running"].includes(inspection.input_preparation.phase) &&
        !consumers.some((job) => ["queued", "running"].includes(job.state)))
    )
      continue;
    attached.set(id, inspection);
  }
  const linked = new Map();
  for (const job of jobs) {
    const inspection = attached.get(job.activity_inspection?.job_id);
    if (inspection) linked.set(job.id, inspection);
  }
  return jobs
    .filter((job) => !attached.has(job.id))
    .map((job) => ({
      ...job,
      ...(linked.has(job.id)
        ? { inspection_stage: inspectionStage(linked.get(job.id)) }
        : {}),
      ...(Array.isArray(job.ucloud_batch_members)
        ? {
            ucloud_batch_members: job.ucloud_batch_members.map((member) => ({
              ...member,
              inspection_stage: inspectionStage(linked.get(member.job_id)),
            })),
          }
        : {}),
      ...(Array.isArray(job.runtime_batch?.members)
        ? {
            runtime_batch: {
              ...job.runtime_batch,
              members: job.runtime_batch.members.map((member) => ({
                ...member,
                inspection_stage: inspectionStage(linked.get(member.job_id)),
              })),
            },
          }
        : {}),
    }));
}
function activityGroup(members) {
  const observed = members,
    states = new Set(observed.map((member) => member.state)),
    latest = observed.reduce((newest, member) =>
      Date.parse(member.updated) > Date.parse(newest.updated) ? member : newest,
    ),
    complete = members.every(
      (member) => member.activity_membership_complete !== false,
    );
  if (!complete)
    members = members.slice(
      0,
      Math.min(
        ...members.map((member) =>
          Number.isSafeInteger(member.activity_group_limit) &&
          member.activity_group_limit > 0
            ? member.activity_group_limit
            : 1000,
        ),
      ),
    );
  const key = activityGroupKey(members[0]),
    upload = key.startsWith("upload-"),
    inspection = key.startsWith("inspection-"),
    request = key.startsWith("request-"),
    fileCount = new Set(
      members.map((member) => member.recording_id || member.id),
    ).size,
    requestCount = new Set(
      members.map((member) => member.activity_parent_job_id || member.id),
    ).size;
  return {
    ...members[0],
    state:
      ["running", "queued", "failed", "interrupted", "cancelled"].find(
        (value) => states.has(value),
      ) || members[0].state,
    progress:
      members.reduce(
        (total, member) =>
          total +
          (member.state === "completed" ? 1 : Number(member.progress) || 0),
        0,
      ) / members.length,
    updated: latest.updated,
    recording_name: upload
      ? `Media preparation — ${members.length} ${members.length === 1 ? "file" : "files"}${complete ? "" : " shown"}`
      : inspection
        ? `Original inspection — ${members.length} ${members.length === 1 ? "file" : "files"}${complete ? "" : " shown"}`
        : `${fileCount} ${fileCount === 1 ? "file" : "files"}${complete ? "" : " shown"} in one request${requestCount !== fileCount ? ` · ${requestCount} model requests` : ""}`,
    activity_group: {
      kind: upload
        ? "upload"
        : inspection
          ? "inspection"
          : request
            ? "request"
            : "intent",
      key,
      members,
      file_count: fileCount,
      request_count: requestCount,
      complete,
    },
  };
}
function inputPreparationOutcome(input) {
  if (input.kind === "inspection") {
    if (input.phase === "pending") return "waiting";
    if (input.phase === "running") return "inspecting";
    if (input.phase === "ready") return "ready";
    return input.error_code === "cancelled" ? "cancelled" : "failed";
  }
  if (input.phase === "waiting")
    return ["pending", "running"].includes(input.source_state)
      ? "inspecting"
      : "waiting";
  if (input.phase === "materializing") return "preparing";
  if (input.error_code === "needs_confirmation")
    return inputPreparationContinuation(input)
      ? "continued"
      : "needs_confirmation";
  return input.error_code === "cancelled" ? "cancelled" : "failed";
}
function activityIncompleteNote(job) {
  return job.activity_membership_complete === false ||
    job.activity_group?.complete === false
    ? '<p class="settings-note" role="alert">This group exceeds the Activity display limit. Its membership is incomplete here; use the individual file controls. Group Stop, Remove and Clear are unavailable.</p>'
    : "";
}
// Routing reasons and UCloud links fold into a collapsed Details when the release profile
// hides inline diagnostics (ADR 0145); an uncertain submission's run name stays visible.
const openActivityDetails = new Set();
$("#jobs").addEventListener(
  "toggle",
  (event) => {
    const id = event.target.dataset?.activityDetails;
    if (!id) return;
    if (event.target.open) openActivityDetails.add(id);
    else openActivityDetails.delete(id);
  },
  true,
);
function activityDiagnostics(job, markup) {
  if (!markup) return "";
  return `<details class="activity-details" data-activity-details="${h(job.id)}" ${openActivityDetails.has(job.id) ? "open" : ""}><summary>Details</summary>${markup}</details>`;
}
function activityJobDetails(job) {
  const allocation = job.ucloud_batch_id
      ? '<p class="field-note">One shared UCloud allocation</p>'
      : "",
    routing = job.routing_reason
      ? `<p class="field-note">Automatic: ${h(job.routing_reason)}</p>`
      : "",
    changed =
      job.source_changed && !job.realign
        ? '<p class="field-note">The source transcript was edited after this was queued; those edits are not in the enhanced result.</p>'
        : "",
    uncertain = job.ucloud_submission_uncertain
      ? `<p>Find this name in UCloud:<br>${h(job.runtime_batch ? "CBS Runtime batch " + job.runtime_batch.id : job.ucloud_batch_id ? "CBS Transcriber batch " + job.ucloud_batch_id : (job.kind === "enhance" ? "CBS enhancement " : "CBS Transcriber ") + job.id)}</p>`
      : "",
    link = job.ucloud_job_id
      ? `<a href="${h(job.ucloud_server)}/app/jobs/properties/${encodeURIComponent(job.ucloud_job_id)}" target="_blank" rel="noopener">Open UCloud job ↗</a>`
      : "";
  return featureOn("activity_diagnostics")
    ? allocation + routing + changed + uncertain + link
    : changed +
        uncertain +
        activityDiagnostics(job, allocation + routing + link);
}
function activityRemoteMember(job) {
  return (
    !job.input_preparation &&
    !!(
      job.execution === "ucloud" ||
      job.ucloud_server ||
      job.ucloud_job_id ||
      job.ucloud_submission_uncertain ||
      job.ucloud_cleanup_state
    )
  );
}
function workflowRequestCard(job, expanded) {
  const group = job.activity_group,
    members = group.members,
    files = members
      .map((member) => {
        const input = member.input_preparation,
          outcome = input
            ? INPUT_OUTCOMES[inputPreparationOutcome(input)]
            : member.state,
          label = input
            ? "Source preparation"
            : member.kind === "enhance"
              ? "Enhancement"
              : "Transcription",
          provider = configurationLabel(member.provider_name, member.model),
          actions = input
            ? inputPreparationActions(member, ["cancel", "retry"])
            : activityRemoteMember(member)
              ? ""
              : jobActions(member);
        return `<li data-workflow-member="${h(member.id)}"><div><a href="#/recordings/${encodeURIComponent(member.recording_id)}">${h(member.recording_name)}</a><span class="badge ${["queued", "running"].includes(member.state) ? "working" : ["failed", "interrupted"].includes(member.state) ? "failed" : ""}">${h(outcome)}</span></div><p>${h(label)}${provider ? ` · ${h(provider)}` : ""}</p>${member.inspection_stage ? `<p>${h(member.inspection_stage)}</p>` : ""}${member.message ? `<p>${h(member.message)}</p>` : ""}${actions ? `<div class="job-footer">${actions}</div>` : ""}</li>`;
      })
      .join(""),
    details = members
      .map((member) => {
        if (member.input_preparation) return "";
        const remote = activityRemoteMember(member),
          details = activityJobDetails(member);
        if (!remote && !details) return "";
        return `<section class="batch-members" data-workflow-details="${h(member.id)}" ${remote ? `data-workflow-remote="${h(member.id)}"` : ""}><p><strong>${h(member.recording_name)}</strong> · ${h(configurationLabel(member.provider_name, member.model) || (member.kind === "enhance" ? "Enhancement" : "Transcription"))}</p>${remote && member.message ? `<p>${h(member.message)}</p>` : ""}${details}${remote ? `<progress value="${member.progress}" max="1" aria-label="UCloud job progress"></progress><div class="job-footer"><span class="badge ${["queued", "running"].includes(member.state) ? "working" : ["failed", "interrupted"].includes(member.state) ? "failed" : ""}">${h(member.state)}</span>${jobActions(member)}</div>` : ""}</section>`;
      })
      .join(""),
    working = members.filter((member) =>
      ["queued", "running"].includes(member.state),
    ).length,
    eligible = members.find(
      (member) =>
        member.input_preparation?.can_prepare === true &&
        !inputPreparationContinuation(member.input_preparation),
    );
  return `<article class="job" data-workflow-request="${h(group.key)}">${activityCardHead(job, "Batch workflow")}<h3>${h(job.recording_name)}</h3><p>${working ? `${working} ${working === 1 ? "task is" : "tasks are"} in progress or waiting.` : group.complete ? "Every task has an outcome." : "Every shown task has an outcome."}</p>${activityIncompleteNote(job)}${details}<dl class="runtime-counts" aria-label="Workflow counts"><div><dt>${group.complete ? "Files" : "Files shown"}</dt><dd data-workflow-count="files">${group.file_count}</dd></div><div><dt>${group.complete ? "Model requests" : "Model requests shown"}</dt><dd data-workflow-count="requests">${group.request_count}</dd></div><div><dt>${group.complete ? "Stages" : "Stages shown"}</dt><dd data-workflow-count="stages">${members.length}</dd></div></dl><progress value="${job.progress}" max="1" aria-label="Workflow stage progress"></progress><details class="batch-members" data-batch-members="${h(group.key)}" ${expanded.has(group.key) ? "open" : ""}><summary>Per-file outcomes (${group.file_count} files)</summary><ul>${files}</ul></details>${activityRemovalNote(job)}<div class="job-footer"><span class="badge ${working ? "working" : ["failed", "interrupted"].includes(job.state) ? "failed" : ""}">${h(job.state)}</span>${eligible ? inputPreparationActions(eligible, ["prepare"]) : ""}</div></article>`;
}
function inputRequestCard(job, expanded) {
  const group = job.activity_group,
    members = group.members,
    inspection = group.kind === "inspection",
    counts = Object.fromEntries(
      Object.keys(INPUT_OUTCOMES).map((key) => [key, 0]),
    ),
    outcomes = new Map();
  for (const member of members) {
    const outcome = inputPreparationOutcome(member.input_preparation);
    outcomes.set(member.id, outcome);
    counts[outcome]++;
  }
  const working = counts.waiting + counts.inspecting + counts.preparing,
    message = inspection
      ? working
        ? `Inspecting original audio: ${counts.ready} of ${members.length} files ready.`
        : counts.ready === members.length
          ? "All originals inspected."
          : `${counts.ready} of ${members.length} originals are ready; the rest need attention.`
      : counts.needs_confirmation
        ? "Runtime preparation needs your confirmation."
        : working
          ? counts.preparing
            ? "Preparing the transcription request…"
            : "Waiting for original inspection…"
          : counts.continued === members.length
            ? "Preparation confirmation recorded. The new requests' progress appears in Activity."
            : "Source preparation stopped.",
    labels = [
      ...new Set(
        members
          .map(
            (member) =>
              configurationLabel(member.provider_name, member.model) ||
              member.model,
          )
          .filter(Boolean),
      ),
    ],
    provider = labels.length
      ? `<p class="field-note">For ${labels.length === 1 ? h(labels[0]) : `${labels.length} configurations`}</p>`
      : "",
    selection = members.find(
      (member) =>
        member.input_preparation.native_group_size > 1 &&
        ["waiting", "materializing"].includes(member.input_preparation.phase),
    )?.input_preparation,
    note = selection
      ? `<p class="field-note">${h(selection.native_group_waiting)} of ${h(selection.native_group_size)} originals are still being prepared before this selection can start together.</p>`
      : "",
    // Until one confirmation covers every sibling, the first eligible file's
    // frozen token is the group's single Prepare action.
    eligible = members.find(
      (member) =>
        member.input_preparation.can_prepare === true &&
        !inputPreparationContinuation(member.input_preparation),
    ),
    files = members
      .map((member) => {
        const outcome = outcomes.get(member.id),
          continuation = inputPreparationContinuation(member.input_preparation),
          detail = continuation
            ? `Preparation confirmation recorded; ${continuation.job_ids.length} new request(s) accepted.`
            : member.message,
          actions = inputPreparationActions(member, ["cancel", "retry"]);
        return `<li data-input-member="${h(member.id)}"><div><a href="#/recordings/${encodeURIComponent(member.recording_id)}">${h(member.recording_name)}</a><span class="badge ${["waiting", "inspecting", "preparing"].includes(outcome) ? "working" : outcome === "failed" ? "failed" : ""}">${INPUT_OUTCOMES[outcome]}</span></div>${detail ? `<p>${h(detail)}</p>` : ""}${actions ? `<div class="job-footer">${actions}</div>` : ""}</li>`;
      })
      .join(""),
    tally = Object.entries(INPUT_OUTCOMES)
      .filter(([key]) => counts[key])
      .map(
        ([key, label]) =>
          `<div><dt>${label}</dt><dd data-input-count="${key}">${counts[key]}</dd></div>`,
      )
      .join("");
  return `<article class="job" data-input-preparation="${inspection ? "inspection" : "intent"}" data-input-request="${h(group.key)}">${activityCardHead(job, inspection ? "Original inspection" : "Source preparation")}<h3>${h(job.recording_name)}</h3>${provider}<p>${h(message)}</p>${activityIncompleteNote(job)}${note}<dl class="runtime-counts" aria-label="Request file counts"><div><dt>${group.complete ? "Files" : "Files shown"}</dt><dd data-input-count="total">${members.length}</dd></div>${tally}</dl><progress value="${members.length - working}" max="${members.length}" aria-label="Files with a preparation outcome"></progress><details class="batch-members" data-batch-members="${h(group.key)}" ${expanded.has(group.key) ? "open" : ""}><summary>Per-file outcomes (${members.length})</summary><ul>${files}</ul></details>${activityRemovalNote(job)}<div class="job-footer"><span class="badge ${working ? "working" : counts.failed ? "failed" : ""}">${working ? (counts.preparing ? "materializing" : inspection && counts.inspecting ? "running" : "waiting") : "terminal"}</span>${eligible ? inputPreparationActions(eligible, ["prepare"]) : ""}</div></article>`;
}
const UPLOAD_OUTCOMES = {
  done: "Done",
  preparing: "Preparing",
  waiting: "Waiting",
  failed: "Failed",
  cancelled: "Cancelled",
};
function uploadOutcome(job) {
  return job.state === "completed"
    ? "done"
    : job.state === "running"
      ? "preparing"
      : job.state === "queued"
        ? "waiting"
        : job.state === "cancelled"
          ? "cancelled"
          : "failed";
}
function uploadGroupCard(job, expanded) {
  const group = job.activity_group,
    members = group.members,
    counts = Object.fromEntries(
      Object.keys(UPLOAD_OUTCOMES).map((key) => [key, 0]),
    );
  for (const member of members) counts[uploadOutcome(member)]++;
  const active = members.filter((member) =>
      ["queued", "running"].includes(member.state),
    ),
    message =
      counts.preparing || counts.waiting
        ? `Preparing browser playback: ${counts.done} of ${members.length} files done.`
        : counts.done === members.length
          ? "All files are ready for playback."
          : `${counts.done} of ${members.length} files are ready; the rest need attention.`,
    files = members
      .map((member) => {
        const outcome = uploadOutcome(member);
        return `<li data-upload-member="${h(member.id)}"><div><a href="#/recordings/${encodeURIComponent(member.recording_id)}">${h(member.recording_name)}</a><span class="badge ${["preparing", "waiting"].includes(outcome) ? "working" : outcome === "failed" ? "failed" : ""}">${UPLOAD_OUTCOMES[outcome]}</span></div>${member.message ? `<p>${h(member.message)}</p>` : ""}${["failed", "cancelled", "interrupted"].includes(member.state) ? `<div class="job-footer"><button class="button" data-retry="${h(member.id)}">Retry</button></div>` : job.activity_group.complete === false ? `<div class="job-footer">${jobActions(member)}</div>` : ""}</li>`;
      })
      .join(""),
    tally = Object.entries(UPLOAD_OUTCOMES)
      .filter(([key]) => counts[key] || ["done", "preparing"].includes(key))
      .map(
        ([key, label]) =>
          `<div><dt>${label}</dt><dd data-upload-count="${key}">${counts[key]}</dd></div>`,
      )
      .join("");
  return `<article class="job" data-upload-group="${h(group.key)}">${activityCardHead(job, "Media preparation")}<h3>${h(job.recording_name)}</h3><p>${h(message)}</p>${activityIncompleteNote(job)}<dl class="runtime-counts" aria-label="Upload file counts"><div><dt>${group.complete ? "Files" : "Files shown"}</dt><dd data-upload-count="total">${members.length}</dd></div>${tally}</dl><progress value="${job.progress}" max="1" aria-label="Upload preparation progress"></progress><details class="batch-members" data-batch-members="${h(group.key)}" ${expanded.has(group.key) ? "open" : ""}><summary>Per-file outcomes (${members.length})</summary><ul>${files}</ul></details>${activityRemovalNote(job)}<div class="job-footer"><span class="badge ${active.length ? "working" : ["failed", "interrupted"].includes(job.state) ? "failed" : ""}">${h(job.state)}</span>${uploadGroupCancelAction(job)}</div></article>`;
}
function uploadGroupCancelAction(job) {
  const group = job.activity_group,
    active = group.members
      .filter((member) => ["queued", "running"].includes(member.state))
      .map((member) => member.id);
  return active.length && group.complete !== false
    ? `<button class="button" data-upload-key="${h(group.key)}" data-upload-cancel="${h(JSON.stringify(active))}" ${pendingUploadCancels.has(group.key) ? "disabled" : ""}>Cancel ${active.length === 1 ? "1 file" : `${active.length} files`}</button>`
    : "";
}
const pendingUploadCancels = new Set();
async function cancelUploadGroup(button) {
  // Cancels the files rendered as active, each through the existing endpoint.
  const key = button.dataset.uploadKey;
  if (button.disabled || pendingUploadCancels.has(key)) return;
  pendingUploadCancels.add(key);
  button.disabled = true;
  let failure;
  try {
    for (const id of JSON.parse(button.dataset.uploadCancel)) {
      try {
        await api(`/jobs/${encodeURIComponent(id)}/cancel`, {
          method: "POST",
        });
      } catch (error) {
        failure ||= error;
      }
    }
  } finally {
    pendingUploadCancels.delete(key);
  }
  await pollJobs();
  if (failure) throw failure;
}
async function runInputPreparationAction(button) {
  // The rendered token is the complete authority. A transition to a real owner
  // never permits this old control to fall through to generic Stop/Retry.
  const raw = button.dataset.inputToken;
  if (button.disabled) return;
  const token = JSON.parse(raw),
    key = inputPreparationActionKey(token),
    endpoint = token.kind === "intent" ? "input-intents" : "input-inspections",
    body =
      token.kind === "intent"
        ? { epoch: token.epoch, phase: token.phase }
        : { generation: token.generation, epoch: token.epoch };
  if (pendingInputActions.has(key)) return;
  if (token.action === "prepare") {
    const preparation = token.preparation,
      other = preparation.count - preparation.choices.length,
      choices =
        preparation.choices.join("\n") + (other ? `\n…and ${other} more.` : "");
    if (
      !confirm(
        `Prepare the pinned UCloud runtime and public models for ${preparation.count} frozen recording request(s)?\nThis may download several GB and take 20 minutes or longer. No recording is uploaded until the runtime is ready.\nNew jobs use these stored choices; earlier requests and reviewed results are kept. Provider charges may apply.\n\n${choices}`,
      )
    )
      return;
    body.fingerprint = preparation.fingerprint;
    body.confirmed = true;
  }
  pendingInputActions.add(key);
  button.disabled = true;
  let failure;
  try {
    await api(`/${endpoint}/${encodeURIComponent(token.job)}/${token.action}`, {
      method: "POST",
      body,
    });
  } catch (error) {
    failure = error;
  } finally {
    pendingInputActions.delete(key);
    try {
      await pollJobs();
    } catch (error) {
      failure ||= error;
    }
  }
  if (failure) throw failure;
}
const pendingJobCancels = new Set(),
  pendingRuntimeCancels = new Set(),
  pendingInputGroupCancels = new Set();
const pendingLocalCleanup = new Set();
const pendingRuntimePreparations = new Set();
function runtimePreparationId(job) {
  const identifier = job.runtime_preparation_id;
  return typeof identifier === "string" &&
    /^[a-f0-9]{32}$/.test(identifier) &&
    job.kind === "runtime-preparation" &&
    job.id === `runtime-preparation:${identifier}`
    ? identifier
    : null;
}
function runtimePreparationActions(job) {
  const identifier = runtimePreparationId(job);
  if (!identifier || job.archived) return "";
  const button = (action, label) =>
    `<button class="button" data-runtime-preparation-action="${action}" data-runtime-preparation-id="${identifier}" ${pendingRuntimePreparations.has(identifier) || pendingActivityRemovals.has(job.id) ? "disabled" : ""}>${label}</button>`;
  return [
    job.can_cancel ? button("cancel", "Stop runtime preparation") : "",
    job.can_retry && !job.ucloud_submission_uncertain
      ? button("retry", "Retry preparation")
      : "",
    job.can_cleanup ? button("cleanup", "Clean up preparation run files") : "",
    job.can_reconcile ? button("reconcile", "Find UCloud job") : "",
  ].join("");
}
async function runRuntimePreparationAction(button) {
  const identifier = button.dataset.runtimePreparationId,
    action = button.dataset.runtimePreparationAction;
  if (
    button.disabled ||
    !/^[a-f0-9]{32}$/.test(identifier) ||
    pendingRuntimePreparations.has(identifier) ||
    pendingActivityRemovals.has(`runtime-preparation:${identifier}`) ||
    !["cancel", "retry", "cleanup", "reconcile"].includes(action)
  )
    return;
  pendingRuntimePreparations.add(identifier);
  const controls = document.querySelectorAll(
    `#jobs [data-runtime-preparation-id="${identifier}"]`,
  );
  controls.forEach((control) => (control.disabled = true));
  let failure;
  try {
    const path = `/runtime-preparations/${identifier}/${action}`;
    const result = await api(path, {
      method: "POST",
      body: action === "reconcile" ? { confirm_not_created: false } : {},
    });
    if (
      action === "reconcile" &&
      result.status === "not-found" &&
      result.requires_confirmation
    ) {
      if (
        typeof result.fingerprint !== "string" ||
        !result.fingerprint ||
        runtimePreparationId(result.operation || {}) !== identifier ||
        typeof result.operation.run_name !== "string" ||
        !result.operation.run_name
      )
        throw new Error(
          "The absence check changed. Find the UCloud job again before confirming.",
        );
      if (
        confirm(
          `No UCloud job was found for ${result.operation.run_name}. Confirm you independently checked UCloud and found no allocation for this exact run. This records absence; it starts no new allocation.`,
        )
      ) {
        await api(path, {
          method: "POST",
          body: {
            confirm_not_created: true,
            fingerprint: result.fingerprint,
          },
        });
      }
    }
  } catch (error) {
    failure = error;
  } finally {
    pendingRuntimePreparations.delete(identifier);
    controls.forEach((control) => (control.disabled = false));
    try {
      await pollJobs();
    } catch (error) {
      failure ||= error;
    }
  }
  if (failure) throw failure;
}
async function runJobCancel(button) {
  const key = button.dataset.cancelScope;
  if (button.disabled || pendingJobCancels.has(key)) return;
  pendingJobCancels.add(key);
  button.disabled = true;
  try {
    const outcome = await api(`/jobs/${button.dataset.cancel}/cancel`, {
      method: "POST",
    });
    if (outcome.continued_runtime_batches?.length)
      toast(
        "Native batch stopped. Enhancements shared with other files continue in Activity.",
      );
  } finally {
    pendingJobCancels.delete(key);
    button.disabled = false;
    await pollJobs();
  }
}
function jobActions(job) {
  if (job.runtime_preparation_id) return runtimePreparationActions(job);
  if (job.local_deletion) {
    const item = job.local_deletion;
    if (!/^[a-f0-9]{32}$/.test(item.id)) return "";
    return `<button class="button" data-local-delete="${h(item.id)}" ${item.busy || pendingLocalCleanup.has(item.id) ? "disabled" : ""}>${item.committed ? "Retry local cleanup" : "Retry file recovery"}</button>`;
  }
  if (job.input_preparation) return inputPreparationActions(job);
  if (job.remote_release?.kind === "deletion") return remoteReleaseActions(job);
  if (job.runtime_batch) return runtimeBatchActions(job);
  let actions = "";
  if (
    job.ucloud_cleanup_state === "pending" &&
    !["queued", "running"].includes(job.state)
  )
    actions += `<p class="settings-note">Remote cleanup is pending${job.ucloud_cleanup_error ? `: ${h(job.ucloud_cleanup_error)}` : "; removal could not be verified."}</p><button class="button" data-cleanup="${job.id}">Retry remote cleanup</button>`;
  if (job.ucloud_cleanup_state === "trashed")
    actions +=
      '<span class="field-note">UCloud run files moved to UCloud Trash. Empty Trash in UCloud to free the space.</span>';
  if (job.ucloud_cleanup_state === "deleted")
    actions +=
      '<span class="field-note">UCloud input/output files deleted</span>';
  if (
    ["queued", "running"].includes(job.state) ||
    (job.ucloud_job_id &&
      !job.ucloud_stop_requested &&
      !job.ucloud_cancel_requested &&
      ![
        "SUCCESS",
        "FAILURE",
        "EXPIRED",
        "ABORTED",
        "CANCELLED",
        "SUSPENDED",
      ].includes(job.ucloud_remote_state) &&
      ["failed", "interrupted"].includes(job.state))
  )
    actions += `<button class="button" data-cancel="${job.id}" data-cancel-scope="${h(job.ucloud_batch_id || job.id)}" ${pendingJobCancels.has(job.ucloud_batch_id || job.id) ? "disabled" : ""}>${job.ucloud_batch_id ? `Stop UCloud batch (${job.ucloud_batch_count} files)` : job.ucloud_job_id ? "Stop UCloud job" : "Cancel"}</button>`;
  if (["failed", "cancelled", "interrupted"].includes(job.state))
    actions += `<button class="button" data-retry="${job.id}" ${job.ucloud_batch_id ? 'title="Recover unfinished results from the recorded UCloud allocation; completed files are kept."' : ""}>${job.ucloud_batch_id && job.state === "failed" ? "Retry failed" : "Retry"}</button>`;
  if (
    job.ucloud_submission_uncertain &&
    ["failed", "cancelled", "interrupted"].includes(job.state)
  )
    actions += `<button class="button" data-reconnect="${job.id}">Reconnect UCloud job</button><button class="button" data-no-allocation="${job.id}">No job created</button>`;
  return actions;
}
function remoteReleaseActions(job) {
  const release = job.remote_release,
    button = (action, label) =>
      `<button class="button" data-release-action="${action}" data-release-kind="${h(release.owner_kind)}" data-release-owner="${h(release.id)}">${label}</button>`;
  if (["trashed", "deleted"].includes(release.cleanup_state))
    return '<span class="field-note">UCloud run files moved to UCloud Trash. Empty Trash in UCloud to free the space.</span>';
  let actions = `<p class="settings-note">${h(job.ucloud_cleanup_error || (release.waiting_for_stop ? "Waiting for the UCloud job to stop before cleanup." : "Local data deleted; UCloud cleanup is pending."))}</p>`;
  if (release.can_retry) actions += button("retry", "Retry remote cleanup");
  if (release.submission_uncertain)
    actions +=
      button("reconcile", "Find UCloud job") +
      button("absent", "No job created");
  return actions;
}
function runtimeBatchActions(job) {
  const batch = job.runtime_batch,
    button = (action, label) =>
      `<button class="button" data-runtime-action="${action}" data-runtime-batch="${h(batch.id)}" ${action === "cancel" && pendingRuntimeCancels.has(batch.id) ? "disabled" : ""}>${label}</button>`;
  let actions = "";
  if (job.ucloud_cleanup_state === "pending")
    actions += `<p class="settings-note">Remote cleanup is pending${job.ucloud_cleanup_error ? `: ${h(job.ucloud_cleanup_error)}` : "; removal could not be verified."}</p>`;
  if (job.ucloud_cleanup_state === "retained")
    actions +=
      '<p class="settings-note">UCloud run files are retained for recovery.</p>';
  if (job.ucloud_cleanup_state === "trashed")
    actions +=
      '<span class="field-note">UCloud run files moved to UCloud Trash. Empty Trash in UCloud to free the space.</span>';
  if (batch.can_cleanup) actions += button("cleanup", "Retry remote cleanup");
  if (batch.can_discard)
    actions += button(
      "discard",
      batch.discard_confirmed
        ? "Retry confirmed move to Trash"
        : "Discard retained run files",
    );
  if (batch.can_cancel)
    actions += button(
      "cancel",
      `Stop runtime batch (${batch.member_count} files)`,
    );
  if (batch.retryable_members > 0)
    actions += button("retry", "Retry failed files");
  else if (batch.can_retry)
    actions += button(
      "retry",
      batch.retry_kind === "setup" ? "Retry setup upload" : "Reconnect batch",
    );
  if (
    batch.submission_uncertain &&
    ["failed", "cancelled", "interrupted"].includes(job.state)
  )
    actions +=
      button("reconcile", "Find UCloud job") +
      button("absent", "No job created");
  return actions;
}
let runtimeDiscardContext = null;
function runtimeDiscardBusy(busy) {
  const dialog = $("#runtime-discard-dialog");
  dialog.dataset.busy = String(busy);
  dialog.querySelectorAll("button").forEach((button) => {
    button.disabled = busy;
  });
  $("#runtime-discard-submit").disabled =
    busy ||
    !runtimeDiscardContext?.preview ||
    runtimeDiscardContext.needsRefresh;
}
async function previewRuntimeDiscard(batchId) {
  const dialog = $("#runtime-discard-dialog"),
    context = { batchId, preview: null, needsRefresh: false };
  runtimeDiscardContext = context;
  $("#runtime-discard-error").hidden = true;
  $("#runtime-discard-members").replaceChildren();
  $("#runtime-discard-refresh").hidden = true;
  $("#runtime-discard-summary").textContent =
    "Checking the allocation and preserving available outputs…";
  if (!dialog.open) dialog.showModal();
  // Preview can be cancelled while read-only remote checks finish; it never authorizes Trash.
  runtimeDiscardBusy(false);
  try {
    const preview = await api(
      `/runtime-batches/${encodeURIComponent(batchId)}/discard-preview`,
      {
        method: "POST",
      },
    );
    if (runtimeDiscardContext !== context || !dialog.open) return;
    if (
      preview.runtime_batch_id !== batchId ||
      !Array.isArray(preview.members) ||
      !/^[a-f0-9]{64}$/.test(preview.fingerprint)
    )
      throw new Error(
        "The discard preview is incomplete. Refresh it before continuing.",
      );
    context.preview = preview;
    $("#runtime-discard-title").textContent = preview.already_confirmed
      ? "Retry confirmed move to UCloud Trash"
      : "Discard retained run files";
    $("#runtime-discard-submit").textContent = preview.already_confirmed
      ? "Retry move to UCloud Trash"
      : "Move run files to UCloud Trash";
    const missing = preview.members.filter(
      (member) => !member.result_saved,
    ).length;
    $("#runtime-discard-summary").textContent =
      `${preview.members.length} files in this Runtime batch; ${missing} without a saved result.`;
    $("#runtime-discard-members").innerHTML = preview.members
      .map((member) => {
        const partial = Array.isArray(member.partial_stages)
          ? member.partial_stages.length
          : Number(member.partial_stages || 0);
        const unverified = Number(member.unverified_stages || 0);
        return `<li><strong>${h(member.recording_name)}</strong><span>${h(member.state)} · ${member.result_saved ? "Saved result kept" : preview.already_confirmed ? "Missing result — recovery abandoned" : "Missing result — recovery will be abandoned"}${partial ? ` · ${h(partial)} ${partial === 1 ? "stage file with a matching receipt" : "stage files with matching receipts"} preserved locally` : ""}${unverified ? ` · ${h(unverified)} unverified stage ${unverified === 1 ? "file" : "files"} preserved locally` : ""}</span></li>`;
      })
      .join("");
    runtimeDiscardBusy(false);
  } catch (error) {
    if (runtimeDiscardContext !== context || !dialog.open) return;
    context.needsRefresh = true;
    $("#runtime-discard-error").textContent = error.message;
    $("#runtime-discard-error").hidden = false;
    $("#runtime-discard-refresh").hidden = false;
    runtimeDiscardBusy(false);
  }
}
$("#runtime-discard-dialog").addEventListener("cancel", (event) => {
  if (event.currentTarget.dataset.busy === "true") event.preventDefault();
});
$("#runtime-discard-dialog").addEventListener("close", () => {
  runtimeDiscardContext = null;
});
$("#runtime-discard-refresh").addEventListener("click", () => {
  if (runtimeDiscardContext)
    previewRuntimeDiscard(runtimeDiscardContext.batchId);
});
$("#runtime-discard-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const context = runtimeDiscardContext,
    dialog = $("#runtime-discard-dialog");
  if (
    !context?.preview ||
    context.needsRefresh ||
    dialog.dataset.busy === "true"
  )
    return;
  runtimeDiscardBusy(true);
  $("#runtime-discard-error").hidden = true;
  try {
    const outcome = await api(
      `/runtime-batches/${encodeURIComponent(context.batchId)}/discard`,
      {
        method: "POST",
        body: { fingerprint: context.preview.fingerprint, confirmed: true },
      },
    );
    if (runtimeDiscardContext !== context || !dialog.open) return;
    if (outcome.cleanup_state === "trashed") {
      dialog.close();
      toast(
        "Run files moved to UCloud Trash. Local originals and saved results are kept.",
      );
    } else {
      context.needsRefresh = true;
      $("#runtime-discard-summary").textContent =
        "Your discard decision is saved. Moving the run files to UCloud Trash is still pending; check Activity before trying again.";
      $("#runtime-discard-refresh").hidden = false;
      runtimeDiscardBusy(false);
    }
    await pollJobs();
  } catch (error) {
    if (runtimeDiscardContext !== context || !dialog.open) return;
    context.needsRefresh = true;
    $("#runtime-discard-error").textContent = error.message;
    $("#runtime-discard-error").hidden = false;
    $("#runtime-discard-refresh").hidden = false;
    runtimeDiscardBusy(false);
  }
});
function activityJobs(jobs) {
  // Owner observations may be newer than the raw-row snapshot. Their exact
  // membership takes precedence over a stale same-ID preparation projection.
  jobs = attachBatchInspections(jobs);
  const owned = new Set(
    jobs.flatMap(
      (job) => job.runtime_batch?.members?.map((member) => member.job_id) || [],
    ),
  );
  jobs = jobs.filter((job) => !(job.input_preparation && owned.has(job.id)));
  const batches = new Set(),
    requests = new Map(),
    grouped = new Set();
  for (const job of jobs) {
    const key = activityGroupKey(job);
    if (key) requests.set(key, [...(requests.get(key) || []), job]);
  }
  return jobs.flatMap((job) => {
    // One card per multi-file request or upload; members keep their own tokens.
    const request = requests.get(activityGroupKey(job));
    if (request?.length > 1) {
      if (grouped.has(request)) return [];
      grouped.add(request);
      return [activityGroup(request)];
    }
    if (job.input_preparation) return [job];
    if (job.runtime_batch) {
      const batch = job.runtime_batch;
      return [
        {
          ...job,
          ucloud_batch_id: batch.id,
          ucloud_batch_count: batch.member_count,
          ucloud_batch_members: batch.members,
          ucloud_batch_unresolved: batch.unresolved,
        },
      ];
    }
    // Per-file rows remain in state.jobs for the editor; the owner drives Activity.
    if (job.runtime_batch_id) return [];
    if (!job.ucloud_batch_id) return [job];
    if (batches.has(job.ucloud_batch_id)) return [];
    batches.add(job.ucloud_batch_id);
    const members = job.ucloud_batch_members || [];
    const retry = members.find((member) =>
      ["failed", "cancelled", "interrupted"].includes(member.state),
    );
    return [
      {
        ...job,
        id: retry?.job_id || job.id,
        state: job.ucloud_batch_state ?? job.state,
        progress: job.ucloud_batch_progress ?? job.progress,
        message: job.ucloud_batch_message ?? job.message,
        created: job.ucloud_batch_created ?? job.created,
        updated: job.ucloud_batch_updated ?? job.updated,
        recording_name: `Batch transcription of ${job.ucloud_batch_count} files`,
      },
    ];
  });
}
function needsActivityAttention(job) {
  if (job.kind === "export") return !["queued", "running"].includes(job.state);
  if (job.runtime_preparation_id)
    return !runtimePreparationId(job) || job.needs_attention === true;
  if (job.local_deletion) return true;
  if (
    job.activity_membership_complete === false ||
    job.activity_group?.complete === false
  )
    return true;
  if (job.activity_group)
    return job.activity_group.members.some((member) =>
      needsActivityAttention(member),
    );
  if (job.input_preparation) {
    const input = job.input_preparation;
    if (inputPreparationToken(job) && inputPreparationContinuation(input))
      return false;
    return (
      !inputPreparationToken(job) ||
      input.error_code === "invalid_evidence" ||
      input.phase === "error" ||
      input.phase === "terminal" ||
      input.can_retry === true
    );
  }
  return (
    !["queued", "running"].includes(job.state) &&
    (job.ucloud_batch_unresolved ||
      job.ucloud_submission_uncertain ||
      /data-(cleanup|reconnect|no-allocation|cancel)=/.test(jobActions(job)))
  );
}
function runtimeBatchProgress(job) {
  const progress = job.runtime_batch?.file_progress;
  if (!progress) return "";
  const counts = progress.counts,
    labels = {
      total: "Files",
      completed: "Saved",
      failed: "Failed",
      cancelled: "Cancelled",
      waiting: "Waiting",
      running: "Processing",
      collecting: "Saving",
      interrupted: "Interrupted",
      unknown: "Unknown",
    },
    active = progress.active_files || [];
  return `<div class="runtime-file-progress"><dl class="runtime-counts" aria-label="Batch file counts">${Object.entries(
    labels,
  )
    .filter(
      ([key]) =>
        ["total", "completed", "failed", "cancelled", "waiting"].includes(
          key,
        ) || counts[key],
    )
    .map(
      ([key, label]) =>
        `<div><dt>${label}</dt><dd data-runtime-count="${key}">${h(counts[key] || 0)}</dd></div>`,
    )
    .join(
      "",
    )}</dl><p class="runtime-saved">${h(counts.completed)} of ${h(counts.total)} results saved locally</p><progress value="${counts.completed}" max="${Math.max(1, counts.total)}" aria-label="Results saved locally"></progress>${active.length ? `<ul class="runtime-active-files">${active.map((file) => `<li class="runtime-active-file"><strong>${h(file.recording_name)}</strong><span>${h(file.stage_label)}</span></li>`).join("")}</ul><p class="field-note">Speech-recognition percentage is not reported.</p>` : ""}</div>`;
}
function activityCard(job, expanded) {
  if (job.kind === "export") return CBSBackgroundExports.card(job);
  if (job.runtime_preparation_id) {
    const identifier = runtimePreparationId(job),
      progress = Number.isFinite(job.progress)
        ? Math.max(0, Math.min(1, job.progress))
        : null;
    return `<article class="job" data-runtime-preparation="${h(identifier || job.runtime_preparation_id)}">${activityCardHead(job, "UCloud runtime preparation")}<h3>${h(job.recording_name)}</h3><p>${h(job.message)}</p><p class="field-note">Base CBS Runtime packages, baseline public caches and CUDA check. Additional selected ASR profiles and gated speaker models have separate readiness checks.</p>${job.run_name ? `<p class="field-note">UCloud run: ${h(job.run_name)}</p>` : ""}${job.ucloud_state ? `<p class="field-note">UCloud state: ${h(job.ucloud_state)}</p>` : ""}${job.ucloud_job_url ? `<a href="${h(job.ucloud_job_url)}" target="_blank" rel="noopener noreferrer">Open UCloud job ↗</a>` : ""}${job.archived ? '<p class="field-note">Historical preparation; UCloud controls are disabled.</p>' : job.ucloud_cleanup_state === "pending" ? '<p class="settings-note">Preparation run-file cleanup is pending. Shared runtime and models are kept.</p>' : job.ucloud_cleanup_state === "trashed" ? '<p class="field-note">Preparation run files moved to UCloud Trash.</p>' : ""}${job.ucloud_submission_uncertain && !job.archived ? '<p class="settings-note">Submission could not be confirmed. Find this UCloud run before retrying.</p>' : ""}<progress ${progress === null ? "" : `value="${progress}"`} max="1" aria-label="Runtime preparation progress"></progress>${activityRemovalNote(job)}<div class="job-footer"><span class="badge ${["queued", "running"].includes(job.state) ? "working" : ["failed", "interrupted"].includes(job.state) ? "failed" : ""}">${h(job.state)}</span>${jobActions(job)}</div></article>`;
  }
  if (job.local_deletion)
    return `<article class="job" data-local-cleanup="${h(job.local_deletion.id)}">${activityCardHead(job, "Local file cleanup")}<h3>${h(job.recording_name)}</h3><p>${h(job.message)}</p><div class="job-footer"><span class="badge">${job.local_deletion.busy ? "removing files" : "needs attention"}</span>${jobActions(job)}</div></article>`;
  if (job.activity_group)
    return job.activity_group.kind === "upload"
      ? uploadGroupCard(job, expanded)
      : job.activity_group.kind === "request"
        ? workflowRequestCard(job, expanded)
        : inputRequestCard(job, expanded);
  if (job.input_preparation) return inputPreparationCard(job);
  const members = job.ucloud_batch_members;
  const outcomes = members?.length
    ? `<details class="batch-members" data-batch-members="${h(job.ucloud_batch_id)}" ${expanded.has(job.ucloud_batch_id) ? "open" : ""}><summary>Per-file outcomes (${members.length})</summary><ul>${members
        .map((member) => {
          const detail = job.runtime_batch?.file_progress
            ? member.reason || member.stage_label
            : member.message ||
              (member.result_id
                ? "Result saved locally."
                : "Waiting in the local queue");
          return `<li><div>${member.removed || member.state === "removed" ? h(member.recording_name) : `<a href="#/recordings/${encodeURIComponent(member.recording_id)}">${h(member.recording_name)}</a>`}<span class="badge ${["failed", "interrupted"].includes(member.state) ? "failed" : ""}">${h(member.status_label || member.state)}</span></div>${member.inspection_stage ? `<p>${h(member.inspection_stage)}</p>` : ""}${detail ? `<p>${h(detail)}</p>` : ""}</li>`;
        })
        .join("")}</ul></details>`
    : "";
  return `<article class="job" ${job.ucloud_batch_id ? `data-activity-batch="${h(job.ucloud_batch_id)}"` : ""}>${activityCardHead(job, job.kind === "media" ? "Media preparation" : h(configurationLabel(job.provider_name, job.model) || "API transcription"))}<h3>${h(job.recording_name)}</h3><p>${h(job.message || "Waiting in the local queue")}</p>${job.inspection_stage ? `<p>${h(job.inspection_stage)}</p>` : ""}${activityJobDetails(job)}${runtimeBatchProgress(job) || `<progress value="${job.progress}" max="1" aria-label="Job progress"></progress>`}${outcomes}${activityRemovalNote(job)}<div class="job-footer"><span class="badge ${["queued", "running"].includes(job.state) ? "working" : ["failed", "interrupted"].includes(job.state) ? "failed" : ""}">${h(job.state)}</span>${jobActions(job)}</div></article>`;
}
let jobsPollSequence = 0,
  jobsRenderedSequence = 0;
async function pollJobs() {
  const sequence = ++jobsPollSequence,
    latest = await api("/jobs", { background: true });
  if (activityClearedAt === undefined)
    activityClearedAt = await api("/activity", { background: true }).then(
      (activity) => {
        mergeActivityHidden(activity.hidden);
        return activity.cleared_at;
      },
      () => undefined,
    );
  // A late response never replaces a newer rendering, so a card removed from
  // Activity (or any newer state) cannot be resurrected by an older poll.
  if (sequence < jobsRenderedSequence) return;
  jobsRenderedSequence = sequence;
  state.jobs = latest;
  CBSBackgroundExports.observe(latest);
  refreshPassageRepairs();
  const activities = activityJobs(state.jobs);
  $("#job-count").textContent = activities.filter((job) =>
    ["queued", "running"].includes(job.state),
  ).length;
  // Finished realignments of edited passages are routine; Activity keeps the rest.
  const listed = activities.filter(
    (job) =>
      !(job.realign && job.state === "completed" && !job.input_preparation),
  );
  // V105/V162: only the rendered list is filtered; state.jobs stays complete.
  const shown = listed.filter(
    (job) => !clearedFromActivity(job) && !removedFromActivity(job),
  );
  const expanded = new Set(
    [...document.querySelectorAll("[data-batch-members][open]")].map(
      (details) => details.dataset.batchMembers,
    ),
  );
  const attention = shown.filter(
    (job) => needsActivityAttention(job) && !finishedBeforeActivityClear(job),
  );
  const retained = shown.filter(
    (job) => needsActivityAttention(job) && finishedBeforeActivityClear(job),
  );
  const retainedOpen = !!$("#jobs [data-retained-activity][open]");
  const recent = shown.filter((job) => !needsActivityAttention(job));
  $("#jobs").innerHTML = shown.length
    ? (attention.length
        ? `<section class="activity-attention" aria-label="Needs attention"><h3>Needs attention</h3>${attention.map((job) => activityCard(job, expanded)).join("")}</section>`
        : "") +
      (recent.length
        ? `<section aria-label="Recent activity">${attention.length || retained.length ? "<h3>Recent activity</h3>" : ""}${recent.map((job) => activityCard(job, expanded)).join("")}</section>`
        : "") +
      (retained.length
        ? `<details class="batch-members retained-activity" data-retained-activity aria-label="Earlier jobs needing attention" ${retainedOpen ? "open" : ""}><summary>${retained.length} earlier ${retained.length === 1 ? "job needs" : "jobs need"} attention</summary>${retained.map((job) => activityCard(job, expanded)).join("")}</details>`
        : "")
    : listed.length
      ? listed.some((job) => clearedFromActivity(job))
        ? '<p class="empty-jobs">Finished jobs cleared. New jobs will appear here.</p>'
        : '<p class="empty-jobs">Finished jobs removed. New jobs will appear here.</p>'
      : '<p class="empty-jobs">All quiet here. Your jobs will appear when you add or transcribe recordings.</p>';
}
// V162: the × removes one finished card from Activity; nothing is deleted. The
// server records each hidden job's update time, so a later change shows the card
// again, and cards with active work or a remote obligation are never hidden.
const activityHidden = new Map(),
  pendingActivityRemovals = new Set(),
  activityRemovalErrors = new Map(),
  REMOTE_TERMINAL_STATES = [
    "SUCCESS",
    "FAILURE",
    "EXPIRED",
    "ABORTED",
    "CANCELLED",
    "SUSPENDED",
  ];
function mergeActivityHidden(value) {
  // Merge rather than replace: overlapping removals may answer out of order.
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  for (const [id, updated] of Object.entries(value))
    if (typeof updated === "string") activityHidden.set(id, updated);
}
function activityRemovable(job) {
  if (job.kind === "export") return false;
  if (job.runtime_preparation_id)
    return !!runtimePreparationId(job) && job.can_remove === true;
  if (job.local_deletion) return false;
  if (
    job.activity_membership_complete === false ||
    job.activity_group?.complete === false
  )
    return false;
  if (job.activity_group)
    return job.activity_group.members.every((member) =>
      activityRemovable(member),
    );
  if (["queued", "running"].includes(job.state)) return false;
  if (job.input_preparation) {
    const input = job.input_preparation;
    if (!inputPreparationToken(job) || input.error_code === "invalid_evidence")
      return false;
    return input.kind === "intent"
      ? input.phase === "terminal" && input.native_group_resolved === true
      : !["pending", "running"].includes(input.phase);
  }
  return !(
    job.ucloud_batch_unresolved ||
    job.ucloud_submission_uncertain ||
    ["pending", "retained"].includes(job.ucloud_cleanup_state) ||
    (job.state !== "completed" &&
      job.ucloud_job_id &&
      !job.ucloud_stop_requested &&
      !job.ucloud_cancel_requested &&
      !REMOTE_TERMINAL_STATES.includes(job.ucloud_remote_state)) ||
    /data-(cleanup|reconnect|no-allocation|cancel)=|data-release-action=|data-runtime-action="(cleanup|discard|cancel|reconcile|absent)"/.test(
      jobActions(job),
    )
  );
}
function removedFromActivity(job) {
  const members = job.activity_group ? job.activity_group.members : [job];
  return (
    activityRemovable(job) &&
    members.every((member) => activityHidden.get(member.id) === member.updated)
  );
}
function activityCardHead(job, kind) {
  return `<div class="job-head"><span class="job-kind">${kind}</span>${
    activityRemovable(job)
      ? `<button type="button" class="icon-button danger job-remove" data-activity-remove="${h(job.id)}" data-activity-updated="${h(job.updated)}" ${job.runtime_preparation_id ? `data-runtime-preparation-id="${h(job.runtime_preparation_id)}"` : ""} aria-label="Remove from Activity" title="Remove from Activity. Nothing is deleted." ${pendingActivityRemovals.has(job.id) || pendingRuntimePreparations.has(job.runtime_preparation_id) ? "disabled" : ""}>×</button>`
      : activityStopButton(job)
  }</div>`;
}
function activityStopButton(job) {
  if (job.local_deletion || job.remote_release || job.restored_history)
    return "";
  const group = job.activity_group,
    template = document.createElement("template");
  if (group?.complete === false || job.activity_membership_complete === false)
    return "";
  // Copy the existing control, including its frozen token and pending state.
  // The header and footer therefore use exactly the same cancellation path.
  template.innerHTML = group
    ? group.kind === "upload"
      ? uploadGroupCancelAction(job)
      : group.members
          .map((member) =>
            group.kind === "request"
              ? jobActions(member)
              : inputPreparationActions(member, ["cancel"]),
          )
          .join("")
    : jobActions(job);
  const controls = template.content.querySelectorAll(
    '[data-cancel], [data-upload-cancel], [data-input-action="cancel"], [data-runtime-action="cancel"], [data-runtime-preparation-action="cancel"]',
  );
  if (!controls.length) return "";
  const button = controls[0],
    count =
      group?.kind === "upload"
        ? JSON.parse(button.dataset.uploadCancel).length
        : controls.length,
    label = group
      ? group.kind === "request"
        ? `Stop workflow (${count} ${count === 1 ? "task" : "tasks"})`
        : `Stop ${group.kind === "inspection" ? "inspection" : "preparation"} (${count} ${count === 1 ? "file" : "files"})`
      : job.input_preparation
        ? `Stop ${job.input_preparation.kind === "inspection" ? "inspection" : "source preparation"}`
        : job.kind === "media"
          ? "Stop preparation"
          : button.textContent.startsWith("Stop ")
            ? `Stop task: ${button.textContent.slice(5)}`
            : "Stop task";
  if (group && group.kind !== "upload") {
    button.removeAttribute("data-input-action");
    button.removeAttribute("data-input-token");
    button.removeAttribute("data-cancel");
    button.removeAttribute("data-cancel-scope");
    button.dataset.inputGroupCancel = group.key;
    button.disabled = pendingInputGroupCancels.has(group.key);
  }
  button.type = "button";
  button.className = "icon-button danger job-remove";
  button.dataset.activityStop = "";
  button.setAttribute("aria-label", label);
  button.title = `${label}. Progress stays in Activity.`;
  button.textContent = "×";
  return button.outerHTML;
}
function activityRemovalNote(job) {
  const error = activityRemovalErrors.get(job.id);
  return error && error.updated === job.updated
    ? `<p class="settings-note" role="alert" data-activity-remove-error>${h(error.message)}</p>`
    : "";
}
async function removeFromActivity(button) {
  const id = button.dataset.activityRemove;
  if (button.disabled || pendingActivityRemovals.has(id)) return;
  pendingActivityRemovals.add(id);
  activityRemovalErrors.delete(id);
  button.disabled = true;
  try {
    const preparation = button.dataset.runtimePreparationId;
    if (preparation) {
      if (
        !/^[a-f0-9]{32}$/.test(preparation) ||
        id !== `runtime-preparation:${preparation}`
      )
        throw new Error(
          "This preparation could not be identified. Refresh Activity.",
        );
      await api(`/runtime-preparations/${preparation}/hide`, {
        method: "POST",
        body: {},
      });
    } else {
      // A grouped card names one member; the server hides its whole request.
      mergeActivityHidden(
        (await api("/activity/hide", { method: "POST", body: { job_id: id } }))
          .hidden,
      );
    }
  } catch (error) {
    activityRemovalErrors.set(id, {
      updated: button.dataset.activityUpdated,
      message: error.message,
    });
  } finally {
    pendingActivityRemovals.delete(id);
  }
  await pollJobs();
}
// V105: Clear finished hides finished jobs up to now from Activity; nothing is deleted.
// Older cleanup/recovery obligations remain accessible in a collapsed group.
let activityClearedAt;
function finishedBeforeActivityClear(job) {
  if (job.kind === "export") return false;
  return (
    ["completed", "failed", "cancelled", "interrupted", "historical"].includes(
      job.state,
    ) && Date.parse(job.updated) <= Date.parse(activityClearedAt)
  );
}
function clearedFromActivity(job) {
  return finishedBeforeActivityClear(job) && !needsActivityAttention(job);
}
$("#activity-clear").addEventListener(
  "click",
  handle(async () => {
    const button = $("#activity-clear");
    button.disabled = true;
    try {
      activityClearedAt = (await api("/activity/clear", { method: "POST" }))
        .cleared_at;
      $("#jobs [data-retained-activity]")?.removeAttribute("open");
      await pollJobs();
    } finally {
      button.disabled = false;
    }
  }),
);
$("#jobs").addEventListener(
  "click",
  handle(async (event) => {
    const preparation = event.target.closest(
      "[data-runtime-preparation-action]",
    );
    if (preparation) {
      await runRuntimePreparationAction(preparation);
      return;
    }
    const local = event.target.closest("[data-local-delete]");
    if (local) {
      const identifier = local.dataset.localDelete;
      if (pendingLocalCleanup.has(identifier)) return;
      pendingLocalCleanup.add(identifier);
      local.disabled = true;
      try {
        const reply = await api(
          `/local-deletions/${encodeURIComponent(identifier)}/retry`,
          { method: "POST" },
        );
        toast(
          reply.ok
            ? "Local file cleanup complete."
            : reply.local_cleanup.message,
          !reply.ok,
        );
      } finally {
        pendingLocalCleanup.delete(identifier);
        await pollJobs();
      }
      return;
    }
    const remove = event.target.closest("[data-activity-remove]");
    if (remove) {
      await removeFromActivity(remove);
      return;
    }
    const uploadCancel = event.target.closest("[data-upload-cancel]");
    if (uploadCancel) {
      await cancelUploadGroup(uploadCancel);
      return;
    }
    const inputGroup = event.target.closest("[data-input-group-cancel]");
    if (inputGroup) {
      const key = inputGroup.dataset.inputGroupCancel;
      if (inputGroup.disabled || pendingInputGroupCancels.has(key)) return;
      pendingInputGroupCancels.add(key);
      inputGroup.disabled = true;
      const controls = inputGroup
        .closest(".job")
        .querySelectorAll('[data-input-action="cancel"], [data-cancel]');
      let failure;
      try {
        for (const control of controls) {
          try {
            if (control.hasAttribute("data-input-action"))
              await runInputPreparationAction(control);
            else await runJobCancel(control);
          } catch (error) {
            failure ||= error;
          }
        }
      } finally {
        pendingInputGroupCancels.delete(key);
        await pollJobs();
      }
      if (failure) throw failure;
      return;
    }
    const input = event.target.closest("[data-input-action]");
    if (input) {
      await runInputPreparationAction(input);
      return;
    }
    const release = event.target.closest("[data-release-action]");
    if (release) {
      const action = release.dataset.releaseAction,
        kind = release.dataset.releaseKind;
      let body;
      if (action === "absent") {
        if (
          !confirm(
            "Confirm you inspected UCloud and found no allocation for this deleted run. Cleanup will move its owned working folder to UCloud Trash; no new job will start.",
          )
        )
          return;
        body = { not_created: true };
      } else if (action === "reconcile") {
        body = {};
        if (kind === "native_batch") {
          const remoteId = prompt(
            "Enter this run's UCloud job ID. Its unique saved name must match; no new job will start.",
          );
          if (!remoteId?.trim()) return;
          body.remote_id = remoteId.trim();
        }
      }
      release.disabled = true;
      try {
        await api(
          `/remote-releases/${encodeURIComponent(kind)}/${encodeURIComponent(release.dataset.releaseOwner)}/${action === "absent" ? "reconcile" : action}`,
          {
            method: "POST",
            ...(body ? { body } : {}),
          },
        );
        await pollJobs();
      } finally {
        release.disabled = false;
      }
      return;
    }
    const runtime = event.target.closest("[data-runtime-action]");
    if (runtime) {
      const action = runtime.dataset.runtimeAction,
        batchId = runtime.dataset.runtimeBatch;
      if (
        runtime.disabled ||
        (action === "cancel" && pendingRuntimeCancels.has(batchId))
      )
        return;
      if (action === "discard") {
        await previewRuntimeDiscard(runtime.dataset.runtimeBatch);
        return;
      }
      if (
        action === "absent" &&
        !confirm(
          "Confirm you inspected UCloud and found no allocation with this batch's unique name. This records your confirmation; it does not stop any UCloud job.",
        )
      )
        return;
      runtime.disabled = true;
      if (action === "cancel") pendingRuntimeCancels.add(batchId);
      try {
        await api(
          `/runtime-batches/${encodeURIComponent(runtime.dataset.runtimeBatch)}/${action === "absent" ? "reconcile" : action}`,
          {
            method: "POST",
            ...(action === "absent"
              ? { body: { not_created: true } }
              : action === "reconcile"
                ? { body: {} }
                : {}),
          },
        );
        await pollJobs();
      } finally {
        if (action === "cancel") pendingRuntimeCancels.delete(batchId);
        runtime.disabled = false;
      }
      return;
    }
    const cancel = event.target.closest("[data-cancel]"),
      retry = event.target.closest("[data-retry]"),
      reconnect = event.target.closest("[data-reconnect]"),
      absent = event.target.closest("[data-no-allocation]"),
      cleanup = event.target.closest("[data-cleanup]");
    if (cleanup) {
      const result = await api(
        `/jobs/${cleanup.dataset.cleanup}/cleanup-ucloud`,
        { method: "POST" },
      );
      toast(
        result.cleanup_state === "pending"
          ? "Remote cleanup is pending; see Activity for the reason."
          : "UCloud run files moved to UCloud Trash.",
      );
      await pollJobs();
    }
    if (reconnect) {
      const remoteId = prompt(
        "Find this run in UCloud and enter its job ID. Its unique CBS name must match this local run. No job or upload will be started.",
      );
      if (remoteId?.trim())
        await api(`/jobs/${reconnect.dataset.reconnect}/reconcile`, {
          method: "POST",
          body: { remote_id: remoteId.trim() },
        });
    }
    if (
      absent &&
      confirm(
        "Confirm you inspected UCloud and found no allocation for this local run. This records your confirmation; it does not check or stop UCloud jobs. An explicit retry may then allocate a job.",
      )
    )
      await api(`/jobs/${absent.dataset.noAllocation}/reconcile`, {
        method: "POST",
        body: { not_created: true },
      });
    if (cancel) {
      await runJobCancel(cancel);
      return;
    }
    if (
      retry &&
      (state.jobs.find((job) => job.id === retry.dataset.retry)?.kind ===
        "enhance" ||
        confirm(
          "Retry using this job’s original provider settings? Completed chunks are kept and a separate result will be saved. Provider charges may apply.",
        ))
    )
      await api(`/jobs/${retry.dataset.retry}/retry`, {
        method: "POST",
        body: { replace: true },
      });
    await pollJobs();
  }),
);
// T69: ready timing for an edited passage is added to the same result as a new version,
// only while no draft, dialog or save is open, so it never races the researcher's edits.
let realignApplying = false;
async function applyRealignments() {
  const recording = state.recording;
  if (
    !recording ||
    realignApplying ||
    state.dirty ||
    state.editing ||
    $("#save-status").textContent === "Saving…" ||
    document.querySelector("dialog[open]") ||
    pendingResultMutations.has(recording.id)
  )
    return;
  const job = state.jobs.find(
    (j) =>
      j.realign &&
      j.realign_ready &&
      j.recording_id === recording.id &&
      j.source_result_id === recording.active_transcript_id,
  );
  if (!job) return;
  realignApplying = true;
  job.realign_ready = false;
  const context = recordingContext();
  try {
    const updated = await api(`/recordings/${recording.id}/realign/${job.id}`, {
      method: "POST",
      body: {
        revision: recording.revision,
        result_id: recording.active_transcript_id,
      },
    });
    const warning =
      typeof updated.working_follow_warning === "string"
        ? updated.working_follow_warning.trim()
        : "";
    const message = warning
      ? `Word timing added. ${warning}`
      : "Word timing added. History can undo it.";
    if (
      warning &&
      context.epoch === viewEpoch &&
      context.id === state.recording?.id &&
      context.result === state.recording?.active_transcript_id
    )
      toast(
        updated.snapshot_error
          ? `${updated.snapshot_error} ${message}`
          : message,
        true,
      );
    if (!acceptRecording(context, updated) || state.dirty || state.editing)
      return;
    renderEditorDetails();
    renderTranscript();
    if (warning)
      saved(
        updated.snapshot_error
          ? `${updated.snapshot_error} ${message}`
          : message,
      );
    else {
      saved();
      toast(message);
    }
  } catch {
    // Stale or superseded timing is marked on the server; a newer edit brings its own.
  } finally {
    realignApplying = false;
  }
}
function realigning(segment) {
  return state.jobs.some(
    (job) =>
      job.realign &&
      job.recording_id === state.recording?.id &&
      job.segment_id === segment.id &&
      ["queued", "running"].includes(job.state),
  );
}
async function loadEditingSettings() {
  const settings = await api("/editing-settings");
  $("#realign-edits").checked = settings.realign_edits;
}
$("#realign-edits").addEventListener(
  "change",
  handle(async (event) => {
    const box = event.currentTarget;
    try {
      const settings = await api("/editing-settings", {
        method: "PUT",
        body: { realign_edits: box.checked },
      });
      box.checked = settings.realign_edits;
      toast(
        settings.realign_edits
          ? "Edited passages get new word timing automatically on this computer."
          : "Automatic realignment is off; use Add word timing on a passage instead.",
      );
    } catch (error) {
      box.checked = !box.checked;
      throw error;
    }
  }),
);
async function poll() {
  if (state.polling) return;
  state.polling = true;
  try {
    const previousJobs = JSON.stringify(state.jobs);
    await pollJobs();
    await applyRealignments();
    const changed = previousJobs !== JSON.stringify(state.jobs);
    if (changed) {
      await refreshProjects();
      if (!state.recording && state.project) await refreshRecordings();
    }
    // Completion can arrive while a draft/dialog blocks refreshing the editor.
    // Keep detecting its unseen result after the job list itself stops changing.
    const unseenResult =
      state.recording &&
      state.jobs.some(
        (job) =>
          job.recording_id === state.recording.id &&
          job.state === "completed" &&
          job.result_id &&
          !state.recording.transcripts.some(
            (result) => result.id === job.result_id,
          ),
      );
    if (changed || unseenResult) {
      if (
        state.recording &&
        !state.dirty &&
        !state.editing &&
        !$("#word-dialog").open &&
        !$("#speaker-dialog").open &&
        !$("#results-dialog").open &&
        !pendingResultMutations.has(state.recording.id)
      ) {
        const context = recordingContext();
        const current = await api(`/recordings/${context.id}`);
        if (
          !sameRecordingView(context) ||
          state.dirty ||
          state.editing ||
          $("#word-dialog").open ||
          $("#speaker-dialog").open ||
          $("#results-dialog").open ||
          pendingResultMutations.has(context.id)
        )
          return;
        if (
          current.revision !== state.recording.revision ||
          current.status !== state.recording.status ||
          current.transcripts.length !== state.recording.transcripts.length
        ) {
          const readyNow =
            current.status === "ready" && state.recording.status !== "ready";
          if (!acceptRecording(context, current)) return;
          if (readyNow) await openEditor();
          else {
            renderEditorDetails();
            renderTranscript();
          }
        }
      }
    }
  } catch {
    /* A stopped server is retried without interrupting unsaved local edits. */
  } finally {
    state.polling = false;
  }
}
handle(async () => {
  await loadProviders();
  await loadEditingSettings();
  await refreshProjects();
  await route();
  await pollJobs();
  setInterval(poll, 1600);
})();
