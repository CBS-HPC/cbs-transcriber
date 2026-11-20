"use strict";

// Spelling observes one open Text draft. It never writes saved transcript text (ADR 0185).
// Possible issues are coloured in a layer behind the textarea; Ctrl+click (⌘+click on a Mac)
// or Alt+Enter inside one opens a small suggestion popover, and a chosen word is ordinary
// input, so native Undo restores it. Public dictionaries download in Activity (ADR 0187).
// The release profile's "spelling" feature hides it: static entry points carry
// data-feature="spelling", and every dynamic entry point checks featureOn first.
let spellingView = null,
  spellingGeneration = 0,
  spellingPreferences = null,
  spellingSettingsGeneration = 0,
  spellingVocabularyView = null,
  spellingFinishedTasks = null;
const spellingOptOuts = new Set(),
  pendingSpellingTasks = new Set(),
  spellingMac = /Mac|iPhone|iPad/u.test(navigator.platform || ""),
  spellingTrigger = spellingMac ? "⌘+click" : "Ctrl+click";
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
    view.controls.querySelector("[data-spelling-enable]").checked
  );
}
function invalidateSpellingEditor() {
  spellingGeneration++;
  const view = spellingView;
  clearTimeout(view?.timer);
  resetSpellingRetry(view);
  view?.controller?.abort();
  view?.resize?.disconnect();
  view?.popover?.node.remove();
  view?.controls.remove();
  view?.overlay.remove();
  spellingView = null;
}
// No visible status line: screen readers hear the live region; the toggle's tooltip
// and its data-spelling-state colour say the same for pointer users.
function spellingStatus(view, text, mode = "") {
  if (!currentSpelling(view)) return;
  const status = view.controls.querySelector("[data-spelling-status]"),
    label = view.controls.querySelector(".passage-spelling-toggle");
  if (status.textContent !== text) status.textContent = text;
  if (label.title !== text) label.title = text;
  view.controls.dataset.spellingState = mode;
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
  const editor = input.closest(".passage-editor"),
    surface = input.closest(".passage-text-surface"),
    controls = document.createElement("span");
  controls.className = "passage-spelling";
  controls.dataset.spellingPanel = "";
  controls.dataset.feature = "spelling";
  // One remembered library preference: the same switch applies to every passage.
  controls.innerHTML = `<label class="checkbox-label passage-spelling-toggle"><input type="checkbox" data-spelling-enable disabled>Spelling</label><button type="button" class="button subtle" data-spelling-download hidden>Download dictionary</button><span class="sr-only" data-spelling-status role="status" aria-live="polite"></span>`;
  editor.querySelector(".passage-editor-toolbar").append(controls);
  // Behind the textarea, after the playback highlighting layer: colour only, no text.
  const overlay = document.createElement("div");
  overlay.className = "passage-spelling-overlay";
  overlay.setAttribute("aria-hidden", "true");
  overlay.hidden = true;
  overlay.innerHTML = '<div class="passage-spelling-overlay-content"></div>';
  input.insertAdjacentElement("beforebegin", overlay);
  const view = (spellingView = {
    input,
    surface,
    controls,
    overlay,
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
    popover: null,
  });
  if (typeof ResizeObserver === "function") {
    view.resize = new ResizeObserver(() => {
      if (view !== spellingView) return;
      syncSpellingOverlay(view);
      positionSpellingPopover(view);
    });
    view.resize.observe(input);
  }
  scheduleSpelling(view);
}
function scheduleSpelling(view) {
  clearTimeout(view.timer);
  resetSpellingRetry(view);
  view.controller?.abort();
  view.request++;
  view.text = view.input.value;
  view.flags = [];
  view.checked = false;
  view.truncated = false;
  view.ignored.clear();
  view.suggestions.clear();
  view.busy = new Set();
  closeSpellingPopover(view);
  drawSpellingMarks(view);
  showSpellingDownload(view, false);
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
      "unavailable",
    );
    return;
  }
  view.retryAttempt++;
  spellingStatus(
    view,
    "Waiting for local spelling. You can keep editing.",
    "checking",
  );
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
    const enable = view.controls.querySelector("[data-spelling-enable]");
    enable.disabled = false;
    enable.checked = preferences.enabled;
    if (!preferences.enabled) {
      spellingStatus(
        view,
        "Local spelling is off. Turn on Spelling to colour possible issues.",
        "off",
      );
      return;
    }
    spellingStatus(view, "Checking spelling on this computer…", "checking");
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
      missingSpellingDictionary(view, preferences);
      return;
    }
    view.waitingTask = null;
    if (data.status !== "ready") {
      spellingStatus(
        view,
        spellingStatusText[data.status] ||
          "Spelling is unavailable for this passage.",
        "unavailable",
      );
      return;
    }
    let previous = 0;
    if (
      data.offset_unit !== "utf16" ||
      !Array.isArray(data.flags) ||
      data.flags.some((flag) => {
        const invalid =
          !Number.isSafeInteger(flag.start) ||
          !Number.isSafeInteger(flag.end) ||
          flag.start < previous ||
          flag.end <= flag.start ||
          flag.end > text.length ||
          text.slice(flag.start, flag.end) !== flag.word;
        previous = flag.end;
        return invalid;
      })
    )
      throw new Error(
        "Spelling ranges could not be matched to the current draft.",
      );
    view.flags = data.flags;
    view.truncated = data.truncated === true;
    view.checked = true;
    drawSpellingMarks(view);
  } catch (error) {
    if (
      current() &&
      error.name !== "AbortError" &&
      (!spellingPreferences || spellingEnabled(view))
    ) {
      resetSpellingRetry(view);
      spellingStatus(view, error.message, "unavailable");
    }
  }
}
function spellingDictionaryName(view) {
  return view.dictionary?.name || view.language;
}
function showSpellingDownload(view, visible) {
  const button = view.controls.querySelector("[data-spelling-download]");
  button.hidden = !visible;
  button.disabled = !!view.installing;
}
// The editor's first use asks Activity for its dictionary; with automatic downloads off,
// the Download dictionary button is the explicit consent (ADR 0187).
function missingSpellingDictionary(view, preferences) {
  const name = spellingDictionaryName(view);
  if (view.waitingTask) {
    spellingStatus(
      view,
      `Downloading the public ${name} dictionary. Follow it in Activity; no transcript text is sent.`,
      "downloading",
    );
    return;
  }
  if (!preferences.auto_download || view.dictionaryRequested) {
    showSpellingDownload(view, true);
    spellingStatus(
      view,
      `The public ${name} spelling dictionary is not installed. Choose Download dictionary; only dictionary files are downloaded.`,
      "unavailable",
    );
    return;
  }
  view.dictionaryRequested = true;
  requestSpellingDictionary(view, false);
}
async function requestSpellingDictionary(view, consent) {
  if (spellingLocked(view) || view.installing || !spellingEnabled(view)) return;
  const text = view.text,
    language = view.language,
    name = spellingDictionaryName(view),
    current = () => currentSpelling(view, text) && spellingEnabled(view);
  view.installing = true;
  view.dictionaryRequested = true;
  showSpellingDownload(view, consent);
  spellingStatus(
    view,
    `Downloading the public ${name} dictionary. Follow it in Activity; no transcript text is sent.`,
    "downloading",
  );
  try {
    const outcome = await spellingRequest(
      "/spelling/dictionary-tasks",
      {
        method: "POST",
        body: {
          settings_revision: spellingPreferences.revision,
          consent,
          recording_id: view.recording,
        },
      },
      current,
    );
    if (!current()) return;
    if (Array.isArray(outcome.ready) && outcome.ready.includes(language)) {
      scheduleSpelling(view);
      return;
    }
    const tasks = (outcome.tasks || []).filter(
      (task) => task.spelling_dictionary?.language === language,
    );
    const active = tasks.find((task) =>
      ["queued", "running"].includes(task.state),
    );
    if (active) {
      view.waitingTask = active.id;
      showSpellingDownload(view, false);
      if (typeof pollJobs === "function") pollJobs().catch(() => {});
      return;
    }
    showSpellingDownload(view, true);
    spellingStatus(
      view,
      tasks.some((task) => task.state === "failed")
        ? `The ${name} dictionary download failed. Retry it in Activity, or choose Download dictionary.`
        : `The ${name} dictionary is not ready. Choose Download dictionary to try again.`,
      "unavailable",
    );
  } catch (error) {
    if (current()) {
      showSpellingDownload(view, true);
      spellingStatus(view, error.message, "unavailable");
    }
  } finally {
    view.installing = false;
    if (current())
      view.controls.querySelector("[data-spelling-download]").disabled = false;
    if (!current() && currentSpelling(view)) scheduleSpelling(view);
  }
}
// Called by every Activity poll (app.js pollJobs): resume a waiting editor and refresh
// the open Settings → Spelling list when a download finishes.
function observeSpellingDictionaries(jobs) {
  if (!featureOn("spelling")) return;
  const tasks = jobs.filter((job) => spellingDictionaryId(job)),
    view = spellingView;
  if (view?.waitingTask && currentSpelling(view)) {
    const task = tasks.find((job) => job.id === view.waitingTask);
    if (task?.state === "completed") {
      view.waitingTask = null;
      scheduleSpelling(view);
    } else if (!task || !["queued", "running"].includes(task.state)) {
      view.waitingTask = null;
      showSpellingDownload(view, true);
      spellingStatus(
        view,
        `The ${spellingDictionaryName(view)} dictionary download did not finish. Retry it in Activity, or choose Download dictionary.`,
        "unavailable",
      );
    }
  }
  const finished = tasks
    .filter((job) => !["queued", "running"].includes(job.state))
    .map((job) => `${job.id}:${job.state}`)
    .join();
  if (finished !== spellingFinishedTasks) {
    const changed = spellingFinishedTasks !== null;
    spellingFinishedTasks = finished;
    // Only the dictionary list: unsaved preference checkboxes stay as the user left them.
    if (changed && currentSpellingSettings(spellingSettingsGeneration))
      loadSpellingSettings(true);
  }
}

// ---- Coloured underlines behind the textarea --------------------------------------------
function visibleSpellingFlags(view) {
  return currentSpelling(view) && spellingEnabled(view)
    ? view.flags
        .map((flag, index) => [flag, index])
        .filter(([, index]) => !view.ignored.has(index))
    : [];
}
function drawSpellingMarks(view) {
  if (view !== spellingView) return;
  const content = view.overlay.firstElementChild,
    visible = visibleSpellingFlags(view),
    text = view.text;
  view.overlay.hidden = !visible.length;
  if (!visible.length) {
    content.replaceChildren();
  } else {
    const fragment = document.createDocumentFragment();
    let cursor = 0;
    for (const [flag, index] of visible) {
      fragment.append(document.createTextNode(text.slice(cursor, flag.start)));
      const mark = document.createElement("span");
      mark.className = "spelling-flag";
      mark.dataset.spellingFlag = index;
      mark.textContent = text.slice(flag.start, flag.end);
      fragment.append(mark);
      cursor = flag.end;
    }
    // The trailing zero-width space keeps a final empty line's height.
    fragment.append(document.createTextNode(text.slice(cursor) + "​"));
    content.replaceChildren(fragment);
    syncSpellingOverlay(view);
  }
  const count = visible.length;
  // Only a finished check of this exact draft is announced, never each keystroke.
  if (
    !view.checked ||
    !currentSpelling(view) ||
    spellingPreferences?.enabled !== true
  )
    return;
  spellingStatus(
    view,
    view.truncated
      ? `Only the first possible spelling issues are coloured. More may remain. ${spellingTrigger} a coloured word, or press Alt+Enter inside it, for a suggestion.`
      : count
        ? `${count} possible spelling ${count === 1 ? "issue" : "issues"} coloured. ${spellingTrigger} a coloured word, or press Alt+Enter inside it, for a suggestion.`
        : view.ignored.size
          ? "No remaining spelling issues to review."
          : "No spelling issues found in this draft.",
    count ? "issues" : "ready",
  );
}
function syncSpellingOverlay(view) {
  if (view.overlay.hidden) return;
  const content = view.overlay.firstElementChild,
    input = view.input,
    width = `${input.clientWidth}px`,
    transform = `translate(${-input.scrollLeft}px, ${-input.scrollTop}px)`;
  if (content.style.width !== width) content.style.width = width;
  if (content.style.transform !== transform)
    content.style.transform = transform;
}
function spellingFlagAtCaret(view) {
  const start = view.input.selectionStart,
    end = view.input.selectionEnd;
  return visibleSpellingFlags(view).find(
    ([flag]) => flag.start <= start && end <= flag.end,
  )?.[1];
}
function spellingFlagAtPoint(view, x, y) {
  for (const mark of view.overlay.querySelectorAll("[data-spelling-flag]"))
    for (const rect of mark.getClientRects())
      if (
        x >= rect.left &&
        x <= rect.right &&
        y >= rect.top &&
        y <= rect.bottom
      )
        return Number(mark.dataset.spellingFlag);
  return undefined;
}

// ---- Suggestion popover ------------------------------------------------------------------
function closeSpellingPopover(view, refocus = false) {
  const open = view?.popover;
  if (!open) return;
  view.popover = null;
  const focused = open.node.contains(document.activeElement);
  open.node.remove();
  if (refocus && focused && currentSpelling(view))
    view.input.focus({ preventScroll: true });
}
function openSpellingPopover(view, index, focus) {
  if (
    spellingLocked(view) ||
    !spellingEnabled(view) ||
    !view.flags[index] ||
    view.ignored.has(index)
  )
    return;
  closeSpellingPopover(view);
  const node = document.createElement("div");
  node.className = "spelling-popover";
  node.setAttribute("role", "dialog");
  node.setAttribute("aria-label", `Spelling: ${view.flags[index].word}`);
  node.dataset.spellingPopover = index;
  // Focusable itself, so keyboard focus stays inside while every button is disabled.
  node.tabIndex = -1;
  view.surface.append(node);
  view.popover = {
    node,
    index,
    flag: view.flags[index],
    message: "",
    keyboard: focus,
  };
  renderSpellingPopover(view);
  positionSpellingPopover(view);
  if (focus) focusSpellingPopover(view.popover);
  if (
    view.dictionary?.capabilities?.suggest !== false &&
    !view.suggestions.has(index)
  )
    requestSpellingSuggestion(view, index);
}
function focusSpellingPopover(open, previous = null) {
  const target =
    previous &&
    open.node.querySelector(
      previous === "suggestion"
        ? "[data-spelling-suggestion]"
        : `[data-spelling-action="${previous}"]`,
    );
  (target && !target.disabled
    ? target
    : open.node.querySelector("[data-spelling-suggestion]") ||
      open.node.querySelector("button:not(:disabled)") ||
      open.node
  ).focus({ preventScroll: true });
}
function renderSpellingPopover(view) {
  const open = view.popover;
  if (!open || view.flags[open.index] !== open.flag) return;
  const { node, index, flag } = open,
    suggestions = view.suggestions.get(index),
    busy = view.busy.has(index),
    disabled = busy ? "disabled" : "",
    active = document.activeElement,
    inside = node.contains(active),
    // Keyboard users keep focus in the popover across re-rendering.
    keep = inside || (open.keyboard && active === document.body),
    previous =
      inside && active !== node
        ? active.dataset.spellingAction ||
          (active.dataset.spellingSuggestion === undefined
            ? null
            : "suggestion")
        : null;
  const choices =
    view.dictionary?.capabilities?.suggest === false
      ? '<p class="field-note">This dictionary has no suggestions. Correct the word in the passage.</p>'
      : suggestions
        ? suggestions.length
          ? suggestions
              .map(
                (word, number) =>
                  `<button type="button" class="button primary" data-spelling-suggestion="${number}">${h(word)}</button>`,
              )
              .join("")
          : '<p class="field-note">No suggestions.</p>'
        : '<p class="field-note">Looking for a suggestion…</p>';
  node.innerHTML = `<p class="spelling-popover-word">${h(flag.word)}</p><div class="spelling-suggestions">${choices}</div><div class="spelling-popover-actions"><button type="button" class="button subtle" data-spelling-action="recording" ${disabled}>Add to recording vocabulary</button><button type="button" class="button subtle" data-spelling-action="project" ${disabled}>Add to project vocabulary</button><button type="button" class="button subtle" data-spelling-action="ignore" ${disabled}>Ignore once</button></div><p class="field-note" data-spelling-popover-status role="status">${h(open.message)}</p>`;
  if (keep) focusSpellingPopover(open, previous);
}
function positionSpellingPopover(view) {
  const open = view?.popover;
  if (!open) return;
  const mark = view.overlay.querySelector(
      `[data-spelling-flag="${open.index}"]`,
    ),
    rect = mark?.getClientRects()[0],
    box = view.input.getBoundingClientRect(),
    surface = view.surface.getBoundingClientRect();
  // A word scrolled out of the visible draft closes its popover.
  if (!rect || rect.bottom < box.top || rect.top > box.bottom) {
    closeSpellingPopover(view, true);
    return;
  }
  const left = Math.max(
    0,
    Math.min(rect.left - surface.left, surface.width - open.node.offsetWidth),
  );
  open.node.style.left = `${left}px`;
  open.node.style.top = `${rect.bottom - surface.top + 4}px`;
}
function spellingPopoverMessage(view, index, message) {
  if (view.popover?.index !== index) return;
  view.popover.message = message;
  renderSpellingPopover(view);
}
async function requestSpellingSuggestion(view, index) {
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
  busy.add(index);
  renderSpellingPopover(view);
  try {
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
      JSON.stringify(data.language_guard) !== JSON.stringify(view.languageGuard)
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
  } catch (error) {
    if (current()) spellingPopoverMessage(view, index, error.message);
  } finally {
    busy.delete(index);
    if (sameFlag() && view.busy === busy) renderSpellingPopover(view);
  }
}
function spellingVocabularyPath(scope, owner) {
  return scope === "project"
    ? `/projects/${owner}/spelling/vocabulary`
    : scope === "recording"
      ? `/recordings/${owner}/spelling/vocabulary`
      : "/spelling/vocabulary";
}
async function addSpellingWord(view, index, scope) {
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
  busy.add(index);
  renderSpellingPopover(view);
  try {
    const path = spellingVocabularyPath(
        scope,
        scope === "project" ? view.project : view.recording,
      ),
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
    closeSpellingPopover(view, true);
    scheduleSpelling(view);
  } catch (error) {
    if (current()) spellingPopoverMessage(view, index, error.message);
  } finally {
    busy.delete(index);
    if (sameFlag() && view.busy === busy) renderSpellingPopover(view);
  }
}
// The replacement is ordinary editing input: the browser's own Undo restores the word.
function applySpellingSuggestion(view, index, replacement) {
  const flag = view.flags[index],
    input = view.input;
  if (
    !flag ||
    typeof replacement !== "string" ||
    spellingLocked(view) ||
    !spellingEnabled(view) ||
    input.value.slice(flag.start, flag.end) !== flag.word
  )
    return;
  closeSpellingPopover(view);
  input.focus({ preventScroll: true });
  input.setSelectionRange(flag.start, flag.end);
  let inserted = false;
  try {
    inserted = document.execCommand("insertText", false, replacement);
  } catch {
    inserted = false;
  }
  if (!inserted && input.value.slice(flag.start, flag.end) === flag.word) {
    input.setRangeText(replacement, flag.start, flag.end, "end");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
}

// ---- Editor events -----------------------------------------------------------------------
const spellingTranscript = document.getElementById("transcript");
spellingTranscript.addEventListener("input", (event) => {
  if (event.target === spellingView?.input) scheduleSpelling(spellingView);
});
spellingTranscript.addEventListener(
  "scroll",
  (event) => {
    const view = spellingView;
    if (!view) return;
    if (event.target === view.input) syncSpellingOverlay(view);
    if (view.popover) positionSpellingPopover(view);
  },
  true,
);
spellingTranscript.addEventListener("click", (event) => {
  const view = spellingView;
  if (!view) return;
  if (event.target === view.input && (event.ctrlKey || event.metaKey)) {
    const index =
      spellingFlagAtPoint(view, event.clientX, event.clientY) ??
      spellingFlagAtCaret(view);
    if (index === undefined) return;
    event.preventDefault();
    openSpellingPopover(view, index, false);
    return;
  }
  if (event.target.closest("[data-spelling-download]")) {
    if (view.controls.contains(event.target) && !spellingLocked(view))
      requestSpellingDictionary(view, true);
    return;
  }
  const popover = event.target.closest("[data-spelling-popover]");
  if (!popover || popover !== view.popover?.node || spellingLocked(view))
    return;
  const index = view.popover.index,
    suggestion = event.target.closest("[data-spelling-suggestion]"),
    action = event.target.closest("[data-spelling-action]")?.dataset
      .spellingAction;
  if (suggestion)
    applySpellingSuggestion(
      view,
      index,
      view.suggestions.get(index)?.[
        Number(suggestion.dataset.spellingSuggestion)
      ],
    );
  else if (action === "ignore") {
    view.ignored.add(index);
    closeSpellingPopover(view, true);
    drawSpellingMarks(view);
  } else if (action === "recording" || action === "project")
    addSpellingWord(view, index, action);
});
spellingTranscript.addEventListener("keydown", (event) => {
  const view = spellingView;
  if (!view) return;
  if (event.key === "Escape" && view.popover) {
    event.preventDefault();
    event.stopPropagation();
    closeSpellingPopover(view);
    view.input.focus({ preventScroll: true });
    return;
  }
  if (
    event.target === view.input &&
    event.key === "Enter" &&
    event.altKey &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.shiftKey
  ) {
    const index = spellingFlagAtCaret(view);
    if (index === undefined) return;
    event.preventDefault();
    openSpellingPopover(view, index, true);
  }
});
// Pointer or focus anywhere else closes the popover; a later Ctrl+click opens another.
document.addEventListener("pointerdown", (event) => {
  const view = spellingView;
  if (view?.popover && !view.popover.node.contains(event.target))
    closeSpellingPopover(view);
});
document.addEventListener("focusin", (event) => {
  const view = spellingView;
  if (
    view?.popover &&
    event.target !== view.input &&
    !view.popover.node.contains(event.target)
  )
    closeSpellingPopover(view);
});
spellingTranscript.addEventListener("change", async (event) => {
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
  if (!enabled) {
    closeSpellingPopover(view);
    drawSpellingMarks(view);
  }
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
      spellingStatus(view, error.message, "unavailable");
    }
  } finally {
    finishSpellingPreferenceWrite(optOut);
    if (currentSpelling(view)) event.target.disabled = false;
  }
});

// ---- Activity card for dictionary downloads (ADR 0187) -----------------------------------
function spellingDictionaryId(job) {
  const id = job?.spelling_dictionary?.id;
  return job?.kind === "spelling_dictionary" &&
    typeof id === "string" &&
    /^[0-9a-f]{32}$/u.test(id) &&
    job.id === `spelling-dictionary:${id}`
    ? id
    : null;
}
function spellingDictionaryCard(job) {
  const id = spellingDictionaryId(job),
    task = job.spelling_dictionary || {},
    active = ["queued", "running"].includes(job.state),
    busy = !id || pendingSpellingTasks.has(id) ? "disabled" : "",
    progress = Number.isFinite(job.progress)
      ? Math.max(0, Math.min(1, job.progress))
      : null,
    actions = id
      ? `${task.can_retry ? `<button type="button" class="button" data-spelling-task-action="retry" data-spelling-task-id="${id}" ${busy}>Retry download</button>` : ""}${task.can_dismiss ? `<button type="button" class="button" data-spelling-task-action="dismiss" data-spelling-task-id="${id}" ${busy}>Dismiss</button>` : ""}`
      : "";
  return `<article class="job" data-spelling-task="${h(id || job.id)}">${activityCardHead(job, "Spelling dictionary")}<h3>${h(job.recording_name)}</h3><p>${h(job.message)}</p><p class="field-note">Public dictionary files only. No transcript text or vocabulary is sent.</p>${active ? `<progress ${progress === null ? "" : `value="${progress}"`} max="1" aria-label="Dictionary download progress"></progress>` : ""}<div class="job-footer"><span class="badge ${active ? "working" : ["failed", "interrupted"].includes(job.state) ? "failed" : ""}">${h(job.state === "completed" ? "ready" : job.state)}</span><div class="export-task-actions">${actions}</div></div></article>`;
}
document.getElementById("jobs").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-spelling-task-action]");
  if (!button) return;
  const id = button.dataset.spellingTaskId,
    action = button.dataset.spellingTaskAction;
  if (
    !/^[0-9a-f]{32}$/u.test(id) ||
    !["retry", "dismiss"].includes(action) ||
    pendingSpellingTasks.has(id)
  )
    return;
  pendingSpellingTasks.add(id);
  document
    .querySelectorAll(`[data-spelling-task-id="${id}"]`)
    .forEach((control) => {
      control.disabled = true;
    });
  try {
    const outcome = await spellingRequest(
      action === "retry"
        ? `/spelling/dictionary-tasks/${id}/retry`
        : `/spelling/dictionary-tasks/${id}`,
      { method: action === "retry" ? "POST" : "DELETE" },
    );
    // A waiting editor follows the Retry's new download.
    const view = spellingView,
      successor = spellingDictionaryId(outcome);
    if (
      successor &&
      view &&
      currentSpelling(view) &&
      outcome.spelling_dictionary.language === view.language
    ) {
      view.waitingTask = outcome.id;
      showSpellingDownload(view, false);
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    pendingSpellingTasks.delete(id);
    if (typeof pollJobs === "function") await pollJobs().catch(() => {});
  }
});

// ---- Settings → Spelling tab (index.html, data-feature="spelling") ----------------------
const spellingSettings = document.getElementById("spelling-settings");
function currentSpellingSettings(generation) {
  return (
    generation === spellingSettingsGeneration &&
    document.getElementById("settings-dialog").open &&
    state.settingsTab === "spelling"
  );
}
async function showSpellingTab() {
  if (!featureOn("spelling")) return;
  providerFormVersion++;
  document.getElementById("provider-error").hidden = true;
  state.settingsTab = "spelling";
  settingsUnsaved = false;
  document.querySelectorAll("[data-provider-tab]").forEach((button) => {
    const selected = button.dataset.providerTab === "spelling";
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  for (const id of ["provider-fields", "hf-panel", "processing-panel"]) {
    document.getElementById(id).hidden = true;
    document.getElementById(id).disabled = true;
  }
  const panel = document.getElementById("spelling-panel");
  panel.hidden = false;
  panel.disabled = false;
  await loadSpellingSettings();
}
function spellingSettingsLanguageTask(language) {
  return (state.jobs || []).find(
    (job) =>
      spellingDictionaryId(job) &&
      job.spelling_dictionary.language === language &&
      ["queued", "running"].includes(job.state),
  );
}
function spellingDownloadSize(bytes) {
  if (!Number.isSafeInteger(bytes) || bytes <= 0) return "";
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
async function loadSpellingSettings(listOnly = false) {
  if (!featureOn("spelling")) return;
  const generation = listOnly
      ? spellingSettingsGeneration
      : ++spellingSettingsGeneration,
    current = () => currentSpellingSettings(generation),
    controls = listOnly
      ? "#spelling-installed button"
      : "#spelling-settings input, #spelling-settings button";
  document.querySelectorAll(controls).forEach((control) => {
    control.disabled = true;
  });
  try {
    const [preferences, catalog] = await Promise.all([
      listOnly ? null : spellingRequest("/spelling/settings", {}, current),
      spellingRequest("/spelling/dictionaries", {}, current),
    ]);
    if (!current()) return;
    if (preferences) {
      const accepted = acceptSpellingPreferences(preferences);
      document.getElementById("spelling-enabled").checked = accepted.enabled;
      document.getElementById("spelling-auto-download").checked =
        accepted.auto_download;
    }
    document.getElementById("spelling-installed").innerHTML =
      catalog.dictionaries
        .map((dictionary) => {
          const licence = dictionary.licence || {},
            attribution = /^https:\/\//u.test(licence.attribution_url || "")
              ? `<a href="${h(licence.attribution_url)}" target="_blank" rel="noopener noreferrer">Licence and attribution ↗</a>`
              : "",
            present =
              dictionary.installed === true || dictionary.ready === true,
            downloading = spellingSettingsLanguageTask(dictionary.language),
            status = dictionary.ready
              ? "Ready on this computer."
              : present
                ? "Dictionary files need attention. Remove and download again."
                : downloading
                  ? "Downloading in Activity…"
                  : `Not downloaded${spellingDownloadSize(dictionary.download_bytes) ? ` · ${spellingDownloadSize(dictionary.download_bytes)}` : ""}.`,
            suggestions =
              dictionary.capabilities?.suggest === false
                ? " Checks spelling without suggestions."
                : "";
          return `<li data-spelling-dictionary="${h(dictionary.language)}"><strong>${h(dictionary.name)}</strong><span class="field-note">${h(licence.spdx_expression || licence.publisher_expression || "Licence not established")}</span><p class="field-note" data-spelling-dictionary-status>${h(status + suggestions)}</p><div class="spelling-dictionary-actions">${present ? `<button type="button" class="button subtle" data-spelling-remove="${h(dictionary.language)}">Remove dictionary</button>` : `<button type="button" class="button subtle" data-spelling-install="${h(dictionary.language)}" ${downloading ? 'data-spelling-busy=""' : ""}>Download dictionary</button>`}${attribution}</div></li>`;
        })
        .join("");
    if (!listOnly)
      document.getElementById("spelling-settings-status").textContent =
        "Changes apply after Save.";
  } catch (error) {
    if (current())
      document.getElementById("spelling-settings-status").textContent =
        error.message;
  } finally {
    if (current())
      document.querySelectorAll(controls).forEach((control) => {
        control.disabled = control.hasAttribute("data-spelling-busy");
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
          saved.enabled && saved.auto_download
            ? "Spelling settings saved. Missing dictionaries download in Activity."
            : "Spelling settings saved.";
      }
      if (typeof pollJobs === "function") pollJobs().catch(() => {});
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
            control.disabled = control.hasAttribute("data-spelling-busy");
          });
    }
  });
spellingSettings.addEventListener("click", async (event) => {
  const install = event.target.closest("[data-spelling-install]");
  if (install) {
    const generation = spellingSettingsGeneration,
      current = () => currentSpellingSettings(generation);
    install.disabled = true;
    try {
      await spellingRequest(
        "/spelling/dictionary-tasks",
        {
          method: "POST",
          body: {
            settings_revision: spellingPreferences.revision,
            consent: true,
            language: install.dataset.spellingInstall,
          },
        },
        current,
      );
      if (current())
        document.getElementById("spelling-settings-status").textContent =
          "Downloading the dictionary. Follow it in Activity.";
      if (typeof pollJobs === "function") await pollJobs().catch(() => {});
      if (current()) await loadSpellingSettings(true);
    } catch (error) {
      if (current()) {
        document.getElementById("spelling-settings-status").textContent =
          error.message;
        install.disabled = false;
      }
    }
    return;
  }
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
    if (current()) await loadSpellingSettings(true);
    // A removed dictionary is not fetched again automatically for the open editor.
    if (
      editor === spellingView &&
      currentSpelling(editor) &&
      editor.language === button.dataset.spellingRemove
    )
      editor.dictionaryRequested = true;
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

// ---- Private vocabulary dialog (index.html, data-feature="spelling") --------------------
const spellingVocabularyDialog = document.getElementById(
  "spelling-vocabulary-dialog",
);
const spellingVocabularyTitles = {
  personal: ["Personal vocabulary", "Words for every project in this library"],
  project: ["Project vocabulary", "Words for this project's recordings"],
  recording: ["Recording vocabulary", "Words for this recording only"],
};
function currentSpellingVocabulary(view) {
  return (
    !!view &&
    view === spellingVocabularyView &&
    spellingVocabularyDialog.open &&
    view.epoch === viewEpoch
  );
}
async function openSpellingVocabulary(scope, owner) {
  if (
    !featureOn("spelling") ||
    !spellingVocabularyTitles[scope] ||
    (scope !== "personal" && !owner)
  )
    return;
  const view = (spellingVocabularyView = {
      scope,
      owner,
      epoch: viewEpoch,
      original: null,
      pending: false,
      editor: spellingView,
    }),
    current = () => currentSpellingVocabulary(view),
    inherited = document.getElementById("spelling-vocabulary-inherited");
  document.getElementById("spelling-vocabulary-title").textContent =
    spellingVocabularyTitles[scope][0];
  document.getElementById("spelling-vocabulary-label").textContent =
    spellingVocabularyTitles[scope][1];
  document.getElementById("spelling-vocabulary-text").value = "";
  document.getElementById("spelling-vocabulary-text").disabled = true;
  document.getElementById("spelling-vocabulary-project-words").value = "";
  inherited.hidden = scope !== "recording";
  document.getElementById("spelling-vocabulary-save").disabled = true;
  document.getElementById("spelling-vocabulary-cancel").disabled = false;
  document.getElementById("spelling-vocabulary-status").textContent =
    "Loading vocabulary…";
  spellingVocabularyDialog.showModal();
  try {
    const data = await spellingRequest(
      spellingVocabularyPath(scope, owner),
      {},
      current,
    );
    if (!current()) return;
    const words = (value) =>
      Array.isArray(value) && value.every((word) => typeof word === "string");
    if (
      !Number.isSafeInteger(data.revision) ||
      data.revision < 0 ||
      !words(data.words) ||
      (scope === "recording" && !words(data.project?.words))
    )
      throw new Error("The vocabulary could not be read safely.");
    view.revision = data.revision;
    view.original = data.words.join("\n");
    document.getElementById("spelling-vocabulary-text").value = view.original;
    if (scope === "recording")
      document.getElementById("spelling-vocabulary-project-words").value =
        data.project.words.join("\n");
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
        spellingVocabularyPath(view.scope, view.owner),
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
// File Settings (ADR 0186): the open recording's own words and its project's words.
document
  .getElementById("spelling-file-project-vocabulary")
  .addEventListener("click", () =>
    openSpellingVocabulary("project", state.recording?.project_id),
  );
document
  .getElementById("spelling-recording-vocabulary")
  .addEventListener("click", () =>
    openSpellingVocabulary("recording", state.recording?.id),
  );
