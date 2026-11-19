"use strict";

// Spelling observes one open Text draft. It never writes saved transcript text (ADR 0185).
// The release profile's "spelling" feature hides it: static entry points carry
// data-feature="spelling", and every dynamic entry point checks featureOn first.
let spellingView = null,
  spellingGeneration = 0,
  spellingPreferences = null,
  spellingSettingsGeneration = 0,
  spellingVocabularyView = null;
const spellingOptOuts = new Set();
function pauseSpellingForOptOut(enabled) {
  if (enabled) return null;
  const token = {};
  spellingOptOuts.add(token);
  resetSpellingRetry(spellingView);
  spellingView?.controller?.abort();
  return token;
}
function finishSpellingPreferenceWrite(token) {
  spellingOptOuts.delete(token);
  // Preferences belong to the library. Resume only a fresh check of whichever
  // exact draft is current, including an editor opened while opt-out was pending.
  if (!spellingOptOuts.size && spellingView && currentSpelling(spellingView))
    scheduleSpelling(spellingView);
}

async function spellingRequest(path, options = {}, current = () => true) {
  const response = await fetch(`/api${path}`, {
    method: options.method || "GET",
    cache: "no-store",
    signal: options.signal,
    headers: {
      "X-CBS-Request": "1",
      ...CBSLibraries.instanceHeaders(),
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
    ...(options.body ? { body: JSON.stringify(options.body) } : {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (current()) CBSLibraries.stale(data);
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "The spelling request could not be completed.",
    );
  }
  return data;
}
function spellingLanguageKey() {
  return JSON.stringify([
    state.recording?.language_override,
    state.recording?.language_revision,
    recordingProjectLanguage(),
    state.project?.language_revision,
  ]);
}
function currentSpelling(view, text = view?.text) {
  return (
    !!view &&
    view === spellingView &&
    view.generation === spellingGeneration &&
    view.epoch === viewEpoch &&
    view.recording === state.recording?.id &&
    view.result === state.recording?.active_transcript_id &&
    view.revision === state.recording?.revision &&
    view.passage === String(state.editing) &&
    view.languageKey === spellingLanguageKey() &&
    !pendingRecordingLanguages.has(view.recording) &&
    !pendingProjectLanguages.has(view.project) &&
    view.input.isConnected &&
    !view.input.closest('[data-editor-pane="text"]').hidden &&
    view.input.value === text
  );
}
function spellingLocked(view) {
  return (
    !currentSpelling(view) ||
    spellingOptOuts.size > 0 ||
    view.input.disabled ||
    passageTextSaving ||
    timedWordSaving ||
    structureLocked() ||
    pendingResultMutations.has(view.recording) ||
    pendingRecordingLanguages.has(view.recording) ||
    pendingProjectLanguages.has(view.project)
  );
}
function spellingEnabled(view) {
  return (
    spellingPreferences?.enabled === true &&
    spellingOptOuts.size === 0 &&
    view.panel.querySelector("[data-spelling-enable]").checked
  );
}
function invalidateSpellingEditor() {
  spellingGeneration++;
  clearTimeout(spellingView?.timer);
  resetSpellingRetry(spellingView);
  spellingView?.controller?.abort();
  spellingView?.panel.remove();
  spellingView = null;
}
function spellingStatus(view, text) {
  if (currentSpelling(view)) {
    const status = view.panel.querySelector("[data-spelling-status]");
    if (status.textContent !== text) status.textContent = text;
  }
}
async function getSpellingPreferences(current) {
  if (!spellingPreferences)
    acceptSpellingPreferences(
      await spellingRequest("/spelling/settings", {}, current),
    );
  return spellingPreferences;
}
function acceptSpellingPreferences(preferences) {
  if (
    !Number.isSafeInteger(preferences.revision) ||
    preferences.revision < 0 ||
    typeof preferences.enabled !== "boolean" ||
    typeof preferences.auto_download !== "boolean"
  )
    throw new Error("The spelling settings revision cannot be read safely.");
  if (
    !spellingPreferences ||
    preferences.revision >= spellingPreferences.revision
  )
    spellingPreferences = preferences;
  if (!spellingPreferences.enabled) resetSpellingRetry(spellingView);
  return spellingPreferences;
}
function refreshSpellingEditor(force = false) {
  const input = document.querySelector(
    '#transcript .passage-editor [data-editor-pane="text"]:not([hidden]) textarea',
  );
  if (
    !featureOn("spelling") ||
    !input ||
    !state.recording ||
    state.editing === null
  ) {
    invalidateSpellingEditor();
    return;
  }
  if (
    spellingView?.input === input &&
    spellingView.languageKey === spellingLanguageKey() &&
    spellingView.revision === state.recording.revision &&
    spellingView.epoch === viewEpoch
  ) {
    if (force) scheduleSpelling(spellingView);
    return;
  }
  invalidateSpellingEditor();
  const panel = document.createElement("div");
  panel.className = "passage-spelling";
  panel.dataset.spellingPanel = "";
  panel.dataset.feature = "spelling";
  panel.innerHTML = `<label class="checkbox-label"><input type="checkbox" data-spelling-enable disabled>Check spelling locally</label><p class="field-note" data-spelling-status role="status" aria-live="polite"></p><div data-spelling-download-area></div><details data-spelling-issues hidden><summary></summary><ul class="spelling-issues"></ul></details><div class="spelling-vocabulary-actions"><button type="button" class="button subtle" data-spelling-vocabulary="project">Project vocabulary</button><button type="button" class="button subtle" data-spelling-vocabulary="personal">Personal vocabulary</button></div>`;
  // Below the text surface, never inside the highlighting overlay's positioned box.
  (input.closest(".passage-text-surface") || input).insertAdjacentElement(
    "afterend",
    panel,
  );
  const view = (spellingView = {
    input,
    panel,
    generation: spellingGeneration,
    epoch: viewEpoch,
    recording: state.recording.id,
    result: state.recording.active_transcript_id,
    revision: state.recording.revision,
    project: state.recording.project_id,
    passage: String(state.editing),
    languageKey: spellingLanguageKey(),
    text: input.value,
    request: 0,
    flags: [],
    ignored: new Set(),
    suggestions: new Map(),
    busy: new Set(),
  });
  scheduleSpelling(view);
}
function scheduleSpelling(view) {
  clearTimeout(view.timer);
  resetSpellingRetry(view);
  view.controller?.abort();
  view.request++;
  view.text = view.input.value;
  view.flags = [];
  view.truncated = false;
  view.ignored.clear();
  view.suggestions.clear();
  view.busy = new Set();
  view.panel.querySelector("[data-spelling-issues]").hidden = true;
  view.panel.querySelector("[data-spelling-download-area]").replaceChildren();
  view.timer = setTimeout(() => checkSpelling(view), 400);
}
function resetSpellingRetry(view) {
  if (!view) return;
  clearTimeout(view.retryTimer);
  view.retryTimer = null;
  view.retrySince = null;
  view.retryAttempt = 0;
}
function retrySpellingCheck(view, current) {
  const now = performance.now(),
    delay = [400, 800, 1200, 1600][Math.min(3, view.retryAttempt || 0)];
  view.retrySince ??= now;
  if (view.retryAttempt >= 9 || now - view.retrySince + delay > 12000) {
    resetSpellingRetry(view);
    spellingStatus(
      view,
      "Spelling is still busy. Edit this passage to check again.",
    );
    return;
  }
  view.retryAttempt++;
  spellingStatus(view, "Waiting for local spelling. You can keep editing.");
  const timer = setTimeout(() => {
    if (view.retryTimer !== timer) return;
    view.retryTimer = null;
    if (
      current() &&
      spellingPreferences?.enabled === true &&
      !spellingLocked(view)
    )
      checkSpelling(view);
  }, delay);
  view.retryTimer = timer;
}
const spellingStatusText = {
  disabled: "Local spelling is off.",
  no_language:
    "Choose a recording language to use spelling. Language is not guessed.",
  unsupported:
    "No spelling dictionary is available for this recording language.",
  engine_missing:
    "The local spelling engine is unavailable in this installation.",
  busy: "Spelling is busy. Pause typing and try again.",
  too_large:
    "This passage is too long for a spelling check. It has not been truncated.",
  timeout: "The spelling check took too long. Try a shorter passage.",
  check_only:
    "This dictionary checks spelling without suggestions. Correct the word in the passage.",
};
async function checkSpelling(view) {
  if (!currentSpelling(view) || spellingLocked(view) || spellingOptOuts.size)
    return;
  clearTimeout(view.retryTimer);
  view.retryTimer = null;
  const attempt = (view.checkAttempt = (view.checkAttempt || 0) + 1);
  const serial = view.request,
    text = view.text,
    current = () =>
      currentSpelling(view, text) &&
      view.request === serial &&
      view.checkAttempt === attempt,
    checkCurrent = () => current() && spellingEnabled(view);
  try {
    const preferences = await getSpellingPreferences(current);
    if (!current()) return;
    const enable = view.panel.querySelector("[data-spelling-enable]");
    enable.disabled = false;
    enable.checked = preferences.enabled;
    if (!preferences.enabled) {
      spellingStatus(
        view,
        "Local spelling is off. Turn it on to check passage drafts.",
      );
      return;
    }
    spellingStatus(view, "Checking spelling on this computer…");
    view.controller = new AbortController();
    const requestId = `${view.generation}_${serial}`,
      data = await spellingRequest(
        `/recordings/${view.recording}/spelling/check`,
        {
          method: "POST",
          body: { text, request_id: requestId },
          signal: view.controller.signal,
        },
        checkCurrent,
      );
    if (!checkCurrent()) return;
    if (data.request_id !== requestId || data.recording_id !== view.recording)
      throw new Error("The spelling response does not match this passage.");
    view.language = data.language;
    view.dictionary = data.dictionary;
    view.languageGuard = data.language_guard;
    if (data.status === "busy") {
      retrySpellingCheck(view, checkCurrent);
      return;
    }
    resetSpellingRetry(view);
    if (data.status === "not_installed") {
      showSpellingDownload(view, preferences);
      return;
    }
    if (data.status !== "ready") {
      spellingStatus(
        view,
        spellingStatusText[data.status] ||
          "Spelling is unavailable for this passage.",
      );
      return;
    }
    if (
      data.offset_unit !== "utf16" ||
      !Array.isArray(data.flags) ||
      data.flags.some(
        (flag) =>
          !Number.isSafeInteger(flag.start) ||
          !Number.isSafeInteger(flag.end) ||
          flag.start < 0 ||
          flag.end <= flag.start ||
          flag.end > text.length ||
          text.slice(flag.start, flag.end) !== flag.word,
      )
    )
      throw new Error(
        "Spelling ranges could not be matched to the current draft.",
      );
    view.flags = data.flags;
    view.truncated = data.truncated === true;
    drawSpellingIssues(view);
  } catch (error) {
    if (
      current() &&
      error.name !== "AbortError" &&
      (!spellingPreferences || spellingEnabled(view))
    ) {
      resetSpellingRetry(view);
      spellingStatus(view, error.message);
    }
  }
}
function showSpellingDownload(view, preferences) {
  const dictionary = view.dictionary,
    name = dictionary?.name || view.language;
  spellingStatus(
    view,
    `The public ${name} spelling dictionary is not installed. Only dictionary files are downloaded; your passage stays on this computer.`,
  );
  view.panel.querySelector("[data-spelling-download-area]").innerHTML =
    '<button type="button" class="button subtle" data-spelling-download>Download dictionary</button>';
  if (view.installing) {
    view.panel.querySelector("[data-spelling-download]").disabled = true;
    spellingStatus(
      view,
      "The public dictionary download is still in progress. You can keep editing.",
    );
    return;
  }
  if (preferences.auto_download && !view.installAttempted) {
    view.installAttempted = true;
    // Paint the public-download notice before the separate install request.
    requestAnimationFrame(() =>
      requestAnimationFrame(() => installSpellingDictionary(view, false)),
    );
  }
}
async function installSpellingDictionary(view, consent) {
  if (spellingLocked(view) || view.installing || !spellingEnabled(view)) return;
  const text = view.text,
    current = () => currentSpelling(view, text) && spellingEnabled(view);
  view.installing = true;
  view.installAttempted = true;
  const button = view.panel.querySelector("[data-spelling-download]");
  if (button) button.disabled = true;
  spellingStatus(
    view,
    `Downloading the public ${view.dictionary?.name || view.language} dictionary. No transcript text is sent.`,
  );
  try {
    const outcome = await spellingRequest(
      `/recordings/${view.recording}/spelling/dictionary`,
      {
        method: "POST",
        body: { settings_revision: spellingPreferences.revision, consent },
      },
      current,
    );
    if (!current()) return;
    if (outcome.status !== "ready" || outcome.ready !== true) {
      spellingStatus(
        view,
        outcome.status === "busy"
          ? "Dictionary download is busy. Choose Download dictionary to try again."
          : "The dictionary is not ready. Choose Download dictionary to try again.",
      );
      return;
    }
    view.panel.querySelector("[data-spelling-download-area]").replaceChildren();
    await checkSpelling(view);
  } catch (error) {
    if (current()) spellingStatus(view, error.message);
  } finally {
    view.installing = false;
    if (current()) {
      const control = view.panel.querySelector("[data-spelling-download]");
      if (control) control.disabled = false;
    }
    if (!current() && currentSpelling(view)) scheduleSpelling(view);
  }
}
function drawSpellingIssues(view) {
  if (!currentSpelling(view)) return;
  const visible = view.flags
      .map((flag, index) => [flag, index])
      .filter(([, index]) => !view.ignored.has(index)),
    details = view.panel.querySelector("[data-spelling-issues]");
  details.hidden = !visible.length;
  details.querySelector("summary").textContent =
    `${visible.length} possible spelling ${visible.length === 1 ? "issue" : "issues"}`;
  details.querySelector("ul").innerHTML = visible
    .map(([flag, index]) => {
      const suggestions = view.suggestions.get(index),
        busy = view.busy.has(index);
      return `<li data-spelling-issue="${index}"><strong>${h(flag.word)}</strong>${view.dictionary?.capabilities?.suggest === false ? '<p class="field-note">Correct this word in the passage.</p>' : ""}<div class="spelling-issue-actions">${view.dictionary?.capabilities?.suggest === false ? "" : `<button type="button" class="button subtle" data-spelling-action="suggest" ${busy ? "disabled" : ""}>Suggestions</button>`}<button type="button" class="button subtle" data-spelling-action="ignore" ${busy ? "disabled" : ""}>Ignore once</button><button type="button" class="button subtle" data-spelling-action="project" ${busy ? "disabled" : ""}>Add to project</button><button type="button" class="button subtle" data-spelling-action="personal" ${busy ? "disabled" : ""}>Add to personal</button></div>${suggestions ? `<div class="spelling-suggestions">${suggestions.length ? suggestions.map((word, number) => `<button type="button" class="button subtle" data-spelling-suggestion="${number}">${h(word)}</button>`).join("") : '<span class="field-note">No suggestions.</span>'}</div>` : ""}</li>`;
    })
    .join("");
  spellingStatus(
    view,
    view.truncated
      ? "Only the first spelling issues are listed. More may remain in this passage."
      : visible.length
        ? "Possible spelling issues are suggestions, not verified errors."
        : view.ignored.size
          ? "No remaining spelling issues to review."
          : "No spelling issues found in this draft.",
  );
}
function spellingVocabularyPath(scope, project) {
  return scope === "project"
    ? `/projects/${project}/spelling/vocabulary`
    : "/spelling/vocabulary";
}
async function spellingIssueAction(view, index, action) {
  const flag = view.flags[index],
    busy = view.busy,
    text = view.text,
    sameFlag = () => currentSpelling(view, text) && view.flags[index] === flag,
    current = () => sameFlag() && spellingEnabled(view);
  if (
    !flag ||
    spellingLocked(view) ||
    busy.has(index) ||
    !spellingEnabled(view)
  )
    return;
  if (action === "ignore") {
    view.ignored.add(index);
    drawSpellingIssues(view);
    return;
  }
  busy.add(index);
  drawSpellingIssues(view);
  try {
    if (action === "suggest") {
      if (view.dictionary?.capabilities?.suggest === false) return;
      const requestId = `${view.generation}_${view.request}_suggest_${index}`,
        data = await spellingRequest(
          `/recordings/${view.recording}/spelling/suggest`,
          { method: "POST", body: { word: flag.word, request_id: requestId } },
          current,
        );
      if (!current()) return;
      if (
        data.request_id !== requestId ||
        data.recording_id !== view.recording ||
        data.language !== view.language ||
        JSON.stringify(data.language_guard) !==
          JSON.stringify(view.languageGuard)
      )
        throw new Error(
          "The spelling language changed. Check this passage again.",
        );
      if (data.status !== "ready")
        throw new Error(
          spellingStatusText[data.status] || "Suggestions are unavailable.",
        );
      if (
        !Array.isArray(data.suggestions) ||
        data.suggestions.length > 5 ||
        data.suggestions.some(
          (word) => typeof word !== "string" || !word || word.length > 256,
        )
      )
        throw new Error("The spelling suggestions were invalid.");
      view.suggestions.set(index, data.suggestions);
    } else if (["project", "personal"].includes(action)) {
      const path = spellingVocabularyPath(action, view.project),
        vocabulary = await spellingRequest(path, {}, current);
      if (!current()) return;
      await spellingRequest(
        path,
        {
          method: "PUT",
          body: {
            revision: vocabulary.revision,
            words: [...new Set([...vocabulary.words, flag.word])],
          },
        },
        current,
      );
      if (!current()) return;
      scheduleSpelling(view);
      return;
    }
    if (current()) drawSpellingIssues(view);
  } catch (error) {
    if (current()) spellingStatus(view, error.message);
  } finally {
    busy.delete(index);
    if (sameFlag() && view.busy === busy) {
      view.panel
        .querySelectorAll(`[data-spelling-issue="${index}"] button`)
        .forEach((button) => {
          button.disabled = false;
        });
    }
  }
}
document.getElementById("transcript").addEventListener("input", (event) => {
  if (event.target === spellingView?.input) scheduleSpelling(spellingView);
});
document.getElementById("transcript").addEventListener("click", (event) => {
  const panel = event.target.closest("[data-spelling-panel]"),
    view = spellingView;
  if (!panel || panel !== view?.panel || spellingLocked(view)) return;
  const vocabulary = event.target.closest("[data-spelling-vocabulary]");
  if (vocabulary) {
    openSpellingVocabulary(vocabulary.dataset.spellingVocabulary, view.project);
    return;
  }
  if (event.target.closest("[data-spelling-download]")) {
    installSpellingDictionary(view, true);
    return;
  }
  const row = event.target.closest("[data-spelling-issue]");
  if (!row || !spellingEnabled(view)) return;
  const index = Number(row.dataset.spellingIssue),
    flag = view.flags[index],
    suggestion = event.target.closest("[data-spelling-suggestion]");
  if (suggestion) {
    const replacement =
      view.suggestions.get(index)?.[
        Number(suggestion.dataset.spellingSuggestion)
      ];
    if (
      !flag ||
      typeof replacement !== "string" ||
      view.input.value.slice(flag.start, flag.end) !== flag.word
    )
      return;
    view.input.setRangeText(replacement, flag.start, flag.end, "end");
    view.input.focus({ preventScroll: true });
    view.input.dispatchEvent(new Event("input", { bubbles: true }));
  } else {
    const control = event.target.closest("[data-spelling-action]");
    if (control)
      spellingIssueAction(view, index, control.dataset.spellingAction);
  }
});
document
  .getElementById("transcript")
  .addEventListener("change", async (event) => {
    if (!event.target.matches("[data-spelling-enable]")) return;
    const view = spellingView;
    if (!view || !currentSpelling(view)) return;
    if (spellingLocked(view)) {
      event.target.checked = spellingPreferences?.enabled === true;
      return;
    }
    const enabled = event.target.checked,
      optOut = pauseSpellingForOptOut(enabled);
    event.target.disabled = true;
    try {
      const preferences = await getSpellingPreferences(() =>
          currentSpelling(view),
        ),
        saved = await spellingRequest(
          "/spelling/settings",
          {
            method: "PUT",
            body: {
              revision: preferences.revision,
              enabled,
              auto_download: preferences.auto_download,
            },
          },
          () => currentSpelling(view),
        );
      acceptSpellingPreferences(saved);
    } catch (error) {
      if (currentSpelling(view)) {
        event.target.checked = spellingPreferences?.enabled === true;
        spellingStatus(view, error.message);
      }
    } finally {
      finishSpellingPreferenceWrite(optOut);
      if (currentSpelling(view)) event.target.disabled = false;
    }
  });

// Static Settings → Local Processing section (index.html, data-feature="spelling").
const spellingSettings = document.getElementById("spelling-settings");
function currentSpellingSettings(generation) {
  return (
    generation === spellingSettingsGeneration &&
    document.getElementById("settings-dialog").open &&
    state.settingsTab === "processing"
  );
}
async function loadSpellingSettings() {
  if (!featureOn("spelling")) return;
  const generation = ++spellingSettingsGeneration,
    current = () => currentSpellingSettings(generation);
  spellingSettings.querySelectorAll("input, button").forEach((control) => {
    control.disabled = true;
  });
  try {
    const [preferences, catalog] = await Promise.all([
      spellingRequest("/spelling/settings", {}, current),
      spellingRequest("/spelling/dictionaries", {}, current),
    ]);
    if (!current()) return;
    const accepted = acceptSpellingPreferences(preferences);
    document.getElementById("spelling-enabled").checked = accepted.enabled;
    document.getElementById("spelling-auto-download").checked =
      accepted.auto_download;
    document.getElementById("spelling-installed").innerHTML =
      catalog.dictionaries
        .filter(
          (dictionary) =>
            dictionary.installed === true || dictionary.ready === true,
        )
        .map((dictionary) => {
          const licence = dictionary.licence || {},
            attribution = /^https:\/\//u.test(licence.attribution_url || "")
              ? `<a href="${h(licence.attribution_url)}" target="_blank" rel="noopener noreferrer">Licence and attribution ↗</a>`
              : "";
          return `<li><strong>${h(dictionary.name)}</strong><span class="field-note">${h(licence.spdx_expression || licence.publisher_expression || "Licence not established")}</span>${dictionary.ready ? "" : '<p class="field-note">Dictionary files need attention. Remove and download again.</p>'}${attribution}<button type="button" class="button subtle" data-spelling-remove="${h(dictionary.language)}">Remove dictionary</button></li>`;
        })
        .join("") ||
      '<li class="field-note">No spelling dictionaries installed.</li>';
    document.getElementById("spelling-settings-status").textContent =
      "Changes apply after Save. Dictionary files are public downloads; they contain no transcript text.";
  } catch (error) {
    if (current())
      document.getElementById("spelling-settings-status").textContent =
        error.message;
  } finally {
    if (current())
      spellingSettings.querySelectorAll("input, button").forEach((control) => {
        control.disabled = false;
      });
  }
}
document
  .getElementById("spelling-settings-save")
  .addEventListener("click", async () => {
    const generation = spellingSettingsGeneration,
      current = () => currentSpellingSettings(generation),
      body = {
        revision: spellingPreferences?.revision,
        enabled: document.getElementById("spelling-enabled").checked,
        auto_download: document.getElementById("spelling-auto-download")
          .checked,
      },
      optOut = pauseSpellingForOptOut(body.enabled);
    spellingSettings.querySelectorAll("input, button").forEach((control) => {
      control.disabled = true;
    });
    try {
      const saved = await spellingRequest(
        "/spelling/settings",
        { method: "PUT", body },
        current,
      );
      acceptSpellingPreferences(saved);
      if (current()) {
        document.getElementById("spelling-settings-status").textContent =
          "Spelling settings saved.";
      }
    } catch (error) {
      if (current())
        document.getElementById("spelling-settings-status").textContent =
          error.message;
    } finally {
      finishSpellingPreferenceWrite(optOut);
      if (current())
        spellingSettings
          .querySelectorAll("input, button")
          .forEach((control) => {
            control.disabled = false;
          });
    }
  });
spellingSettings.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-spelling-remove]");
  if (!button) return;
  const generation = spellingSettingsGeneration,
    editor = spellingView,
    current = () => currentSpellingSettings(generation);
  button.disabled = true;
  try {
    await spellingRequest(
      `/spelling/dictionaries/${encodeURIComponent(button.dataset.spellingRemove)}`,
      {
        method: "DELETE",
        body: { settings_revision: spellingPreferences.revision },
      },
      current,
    );
    if (current()) await loadSpellingSettings();
    if (
      editor === spellingView &&
      currentSpelling(editor) &&
      editor.language === button.dataset.spellingRemove
    )
      editor.installAttempted = true;
  } catch (error) {
    if (current()) {
      document.getElementById("spelling-settings-status").textContent =
        error.message;
      button.disabled = false;
    }
  }
});
document.getElementById("settings-dialog").addEventListener("close", () => {
  if (!document.getElementById("settings-dialog").open)
    spellingSettingsGeneration++;
});

// Static private vocabulary dialog (index.html, data-feature="spelling").
const spellingVocabularyDialog = document.getElementById(
  "spelling-vocabulary-dialog",
);
function currentSpellingVocabulary(view) {
  return (
    !!view &&
    view === spellingVocabularyView &&
    spellingVocabularyDialog.open &&
    view.epoch === viewEpoch
  );
}
async function openSpellingVocabulary(scope, project) {
  if (!featureOn("spelling") || (scope === "project" && !project)) return;
  const view = (spellingVocabularyView = {
      scope,
      project,
      epoch: viewEpoch,
      original: null,
      pending: false,
      editor: spellingView,
    }),
    current = () => currentSpellingVocabulary(view);
  document.getElementById("spelling-vocabulary-title").textContent =
    scope === "project" ? "Project vocabulary" : "Personal vocabulary";
  document.getElementById("spelling-vocabulary-text").value = "";
  document.getElementById("spelling-vocabulary-text").disabled = true;
  document.getElementById("spelling-vocabulary-save").disabled = true;
  document.getElementById("spelling-vocabulary-cancel").disabled = false;
  document.getElementById("spelling-vocabulary-status").textContent =
    "Loading vocabulary…";
  spellingVocabularyDialog.showModal();
  try {
    const data = await spellingRequest(
      spellingVocabularyPath(scope, project),
      {},
      current,
    );
    if (!current()) return;
    if (
      !Number.isSafeInteger(data.revision) ||
      data.revision < 0 ||
      !Array.isArray(data.words) ||
      data.words.some((word) => typeof word !== "string")
    )
      throw new Error("The vocabulary could not be read safely.");
    view.revision = data.revision;
    view.original = data.words.join("\n");
    document.getElementById("spelling-vocabulary-text").value = view.original;
    document.getElementById("spelling-vocabulary-text").disabled = false;
    document.getElementById("spelling-vocabulary-save").disabled = false;
    document.getElementById("spelling-vocabulary-status").textContent =
      "Changes apply after Save.";
  } catch (error) {
    if (current())
      document.getElementById("spelling-vocabulary-status").textContent =
        error.message;
  }
}
function canLeaveSpellingVocabulary() {
  const view = spellingVocabularyView;
  if (!view || !spellingVocabularyDialog.open) return true;
  if (view.pending) {
    toast("Wait for vocabulary to finish saving.", true);
    return false;
  }
  if (
    view.original !== null &&
    document.getElementById("spelling-vocabulary-text").value !==
      view.original &&
    !confirm("Discard these unsaved vocabulary changes?")
  )
    return false;
  spellingVocabularyDialog.close();
  return true;
}
spellingVocabularyDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  canLeaveSpellingVocabulary();
});
spellingVocabularyDialog.addEventListener("close", () => {
  if (!spellingVocabularyDialog.open) spellingVocabularyView = null;
});
window.addEventListener("beforeunload", (event) => {
  const view = spellingVocabularyView;
  if (
    view &&
    spellingVocabularyDialog.open &&
    (view.pending ||
      (view.original !== null &&
        document.getElementById("spelling-vocabulary-text").value !==
          view.original))
  ) {
    event.preventDefault();
    event.returnValue = "";
  }
});
document
  .getElementById("spelling-vocabulary-cancel")
  .addEventListener("click", () => {
    if (!spellingVocabularyView?.pending) spellingVocabularyDialog.close();
  });
document
  .getElementById("spelling-vocabulary-save")
  .addEventListener("click", async () => {
    const view = spellingVocabularyView;
    if (
      !view ||
      !currentSpellingVocabulary(view) ||
      view.pending ||
      view.original === null
    )
      return;
    const text = document.getElementById("spelling-vocabulary-text").value,
      words = text
        .split(/\r?\n/u)
        .map((word) => word.trim())
        .filter(Boolean),
      current = () => currentSpellingVocabulary(view);
    view.pending = true;
    spellingVocabularyDialog
      .querySelectorAll("textarea, button")
      .forEach((control) => {
        control.disabled = true;
      });
    try {
      await spellingRequest(
        spellingVocabularyPath(view.scope, view.project),
        { method: "PUT", body: { revision: view.revision, words } },
        current,
      );
      if (current()) spellingVocabularyDialog.close();
      if (view.editor === spellingView && currentSpelling(view.editor))
        scheduleSpelling(view.editor);
    } catch (error) {
      if (current())
        document.getElementById("spelling-vocabulary-status").textContent =
          error.message;
    } finally {
      view.pending = false;
      if (current())
        spellingVocabularyDialog
          .querySelectorAll("textarea, button")
          .forEach((control) => {
            control.disabled = false;
          });
    }
  });
document
  .getElementById("spelling-personal-vocabulary")
  .addEventListener("click", () => openSpellingVocabulary("personal"));
document
  .getElementById("spelling-project-vocabulary")
  .addEventListener("click", () =>
    openSpellingVocabulary("project", state.project?.id),
  );
