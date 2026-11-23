"use strict";

// Word-timing editor (ADR 0195). The ordinary Text editor shows two boxes with the start and
// end of the word at the cursor. Typed times belong to the passage draft: Save passage sends
// text and times in one request (one History version) and Cancel discards both. The
// textarea's value, selection, focus and native Undo are only read here, never written.
//
// Positions are code points of the textarea's presentation (CR/CRLF read as LF), as in
// savedTextPresentation. A word is at the cursor when the caret is strictly inside it, or
// beside a one-character word (one grapheme has no inside) and no other word. A selection
// must lie within one word. Between words, on a space or across several words the boxes
// stay empty and disabled. No time is ever shown for, or given to, a word the user did
// not type it for: new or changed words start blank (ADR 0166 decides them on Save).

const wordTimeDrafts = new WeakMap();
const wordTimeGraphemes =
  typeof Intl === "object" && typeof Intl.Segmenter === "function"
    ? new Intl.Segmenter(undefined, { granularity: "grapheme" })
    : null;

// "" is a blank box (null); text that is not a number of seconds is undefined.
function wordTimeSeconds(value) {
  const text = String(value).trim().replace(",", ".");
  if (!text) return null;
  if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/u.test(text)) return undefined;
  const number = Number(text);
  return Number.isFinite(number) ? number : undefined;
}
function wordTimeText(number) {
  return Number.isFinite(number) ? String(number) : "";
}
function wordTimeKnown(number) {
  return Number.isFinite(number) ? number : null;
}
function wordTimeBounded(segment) {
  return (
    Number.isFinite(segment.start) &&
    Number.isFinite(segment.end) &&
    segment.end > segment.start
  );
}
// Typed times follow their whitespace-separated token, as Save passage pairs tokens.
function wordTimeSeparated(character) {
  return character === undefined || /\s/u.test(character);
}
function wordTimeSingle(text) {
  if (!wordTimeGraphemes) return Array.from(text).length === 1;
  let count = 0;
  for (const _ of wordTimeGraphemes.segment(text)) if (++count > 1) break;
  return count === 1;
}
function wordTimeCodePoints(text) {
  let count = 0;
  for (const _ of text) count++;
  return count;
}

function wordTimeRecord(segment, editor) {
  let record = wordTimeDrafts.get(editor);
  if (!record || record.segment !== segment) {
    record = {
      segment,
      draft: editor.querySelector(".passage-text-surface textarea").value,
      entries: [],
      composing: false,
      layout: null,
      current: null,
      key: "",
    };
    wordTimeDrafts.set(editor, record);
  }
  return record;
}

function wordTimeEntryDirty(entry) {
  const [start, end] = entry.values.map(wordTimeSeconds);
  return (
    start === undefined ||
    end === undefined ||
    start !== entry.baseline[0] ||
    end !== entry.baseline[1]
  );
}

// Keep typed times on their word while the text around it changes. An edit inside or
// touching the word (a spelling fix, punctuation) keeps them on the corrected word;
// removing or splitting it drops them, with a notice.
function trackWordTimes(record, value) {
  if (value === record.draft) return;
  record.layout = null;
  if (!record.entries.length) {
    record.draft = value;
    return;
  }
  const before = Array.from(record.draft),
    after = Array.from(value),
    limit = Math.min(before.length, after.length);
  let prefix = 0;
  while (prefix < limit && before[prefix] === after[prefix]) prefix++;
  let suffix = 0;
  while (
    suffix < limit - prefix &&
    before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
  )
    suffix++;
  const shift = after.length - before.length,
    tail = before.length - suffix,
    kept = [];
  for (const entry of record.entries) {
    let { start, end } = entry;
    if (end < prefix || (end === prefix && wordTimeSeparated(after[end]))) {
      // Unchanged, before the edit.
    } else if (
      start > tail ||
      (start === tail && wordTimeSeparated(after[start + shift - 1]))
    ) {
      start += shift;
      end += shift;
    } else if (start <= prefix && tail <= end) end += shift;
    else end = start;
    const word = after.slice(start, end).join(""),
      previous = kept[kept.length - 1];
    if (
      end <= start ||
      /\s/u.test(word) ||
      (previous && previous.end > start)
    ) {
      if (wordTimeEntryDirty(entry))
        toast(
          `The times you typed for “${entry.word}” were removed, because the word was deleted or split.`,
          true,
        );
      continue;
    }
    kept.push(Object.assign(entry, { start, end, word }));
  }
  record.entries = kept;
  record.draft = value;
}

// Both lists are sorted and disjoint; extra ranges that overlap a base range are dropped.
function mergeWordTimeRanges(base, extra) {
  const merged = [];
  let index = 0;
  for (const item of extra) {
    while (index < base.length && base[index].end <= item.start)
      merged.push(base[index++]);
    if (index < base.length && base[index].start < item.end) continue;
    merged.push(item);
  }
  while (index < base.length) merged.push(base[index++]);
  return merged;
}

// The words of the current draft: words with typed times, saved words that are provably
// unchanged (the same rule as playback highlighting), and in a changed draft every other
// whitespace-separated token, which Save passage would store as an untimed word.
function wordTimeLayout(segment, record) {
  if (record.layout) return record.layout;
  const saved = savedTextPresentation(segment),
    characters = Array.from(record.draft),
    same = record.draft === saved.text,
    ranges = segment.words.map((word, index) => [
      index,
      saved.offsets[word.char_start],
      saved.offsets[word.char_end],
    ]),
    unchanged = same
      ? ranges
      : draftUnchangedRanges(segment, record.draft, ranges).words;
  let words = mergeWordTimeRanges(
    record.entries.map((entry) => ({
      start: entry.start,
      end: entry.end,
      baseline: entry.baseline,
      entry,
    })),
    unchanged.map(([index, start, end]) => ({
      start,
      end,
      baseline: [
        wordTimeKnown(segment.words[index].start),
        wordTimeKnown(segment.words[index].end),
      ],
    })),
  );
  if (!same) {
    const tokens = [];
    let start = -1;
    for (let index = 0; index <= characters.length; index++) {
      const space =
        index === characters.length || /\s/u.test(characters[index]);
      if (!space && start < 0) start = index;
      else if (space && start >= 0) {
        tokens.push({ start, end: index, baseline: [null, null], fresh: true });
        start = -1;
      }
    }
    words = mergeWordTimeRanges(words, tokens);
  }
  for (const word of words)
    word.text = characters.slice(word.start, word.end).join("");
  record.layout = { words };
  return record.layout;
}

function wordTimeTarget(textarea, layout) {
  const value = textarea.value,
    { selectionStart: from, selectionEnd: to } = textarea,
    high = (code) => code >= 0xd800 && code <= 0xdbff,
    low = (code) => code >= 0xdc00 && code <= 0xdfff,
    splits = (position) =>
      position > 0 &&
      position < value.length &&
      high(value.charCodeAt(position - 1)) &&
      low(value.charCodeAt(position));
  if (splits(from) || splits(to)) return { reason: "between" };
  const words = layout.words;
  let start = wordTimeCodePoints(value.slice(0, from)),
    end = start;
  if (to > from) {
    const selected = value.slice(from, to),
      trimmed = selected.trim();
    if (!trimmed) return { reason: "between" };
    // Whitespace is one code unit per code point.
    start += selected.length - selected.trimStart().length;
    end = start + wordTimeCodePoints(trimmed);
  }
  let first = 0,
    last = words.length;
  while (first < last) {
    const middle = (first + last) >> 1;
    if (words[middle].end < start) first = middle + 1;
    else last = middle;
  }
  const near = [];
  for (let index = first; index < words.length; index++) {
    if (words[index].start > end) break;
    near.push(words[index]);
  }
  if (end > start) {
    const overlapping = near.filter(
      (word) => word.start < end && start < word.end,
    );
    return overlapping.length === 1 &&
      overlapping[0].start <= start &&
      end <= overlapping[0].end
      ? { word: overlapping[0] }
      : { reason: overlapping.length ? "several" : "between" };
  }
  const inside = near.find((word) => word.start < start && start < word.end);
  if (inside) return { word: inside };
  return near.length === 1 && wordTimeSingle(near[0].text)
    ? { word: near[0] }
    : { reason: "between" };
}

function wordTimeEffective(word) {
  const [start, end] = word.entry
    ? word.entry.values.map(wordTimeSeconds)
    : word.baseline;
  return Number.isFinite(start) && Number.isFinite(end) && start < end
    ? [start, end]
    : null;
}

// The server's single-word rules (edit_timed_word), checked here first to name the word.
function wordTimeProblem(segment, layout, word) {
  const [start, end] = word.entry.values.map(wordTimeSeconds);
  if (start === undefined || end === undefined)
    return "type the times in seconds, for example 12.5.";
  if (start === null || end === null)
    return "type both a start and an end time.";
  if (start >= end) return "the start must be before the end.";
  if (start < segment.start || end > segment.end)
    return `the times must be within the passage (${segment.start}–${segment.end} s).`;
  const position = layout.words.indexOf(word);
  for (const [index, other] of layout.words.entries()) {
    const times = index === position ? null : wordTimeEffective(other);
    if (
      times &&
      (index < position
        ? times[0] > start || times[1] > end
        : times[0] < start || times[1] < end)
    )
      return `the times must stay in order with “${other.text}”.`;
  }
  return null;
}

// Refreshes the boxes for the word at the cursor; returns whether typed times are unsaved.
function refreshWordTimes(segment, editor) {
  const box = editor?.querySelector("[data-word-times]");
  if (!box) return false;
  const textarea = editor.querySelector(".passage-text-surface textarea"),
    record = wordTimeRecord(segment, editor);
  trackWordTimes(record, textarea.value);
  const dirty = record.entries.some(wordTimeEntryDirty);
  if (passageTextSaving || timedWordSaving) return dirty;
  const inputs = [...box.querySelectorAll("[data-word-time]")],
    status = box.querySelector("[data-word-times-status]");
  let word = null,
    problem = null,
    message = "";
  if (!wordTimeBounded(segment))
    message = "No word times: this passage has no start and end time.";
  else if (!editableTimedWords(segment))
    message =
      "No word times: this passage has no usable word timing. Use Add word timing first.";
  else if (!record.composing && !textarea.disabled) {
    const layout = wordTimeLayout(segment, record),
      target = wordTimeTarget(textarea, layout);
    word = target.word || null;
    message =
      target.reason === "several"
        ? "Select a single word to see its times."
        : "Put the cursor in a word to see its times.";
    if (word?.entry && wordTimeEntryDirty(word.entry)) {
      problem = wordTimeProblem(segment, layout, word);
      message = problem
        ? `“${word.text}”: ${problem}`
        : `“${word.text}”: unsaved times.`;
    } else if (word)
      message =
        word.fresh && !word.entry
          ? `“${word.text}” is new or changed, so it has no time until you type one.`
          : `“${word.text}”`;
  }
  const key = word ? `${word.start}:${word.end}` : "",
    values = word
      ? word.entry
        ? word.entry.values
        : word.baseline.map(wordTimeText)
      : ["", ""];
  inputs.forEach((input, position) => {
    input.disabled = !word;
    if (input !== document.activeElement || record.key !== key)
      input.value = values[position];
    if (problem) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  });
  record.current = word;
  record.key = key;
  if (status.textContent !== message) status.textContent = message;
  return dirty;
}

// Typed times for Save passage, as code-point ranges of the text actually sent. Throws a
// message naming the word when a typed time is not valid, so nothing is sent.
function wordTimesPayload(segment, editor, text) {
  const record = wordTimeDrafts.get(editor);
  if (!record || record.segment !== segment) return [];
  trackWordTimes(
    record,
    editor.querySelector(".passage-text-surface textarea").value,
  );
  const dirty = record.entries.filter(wordTimeEntryDirty);
  if (!dirty.length) return [];
  const layout = wordTimeLayout(segment, record);
  for (const entry of dirty) {
    const word = layout.words.find((item) => item.entry === entry),
      problem = word
        ? wordTimeProblem(segment, layout, word)
        : "its place in the text could not be found.";
    if (problem) throw new Error(`Not saved: “${entry.word}”: ${problem}`);
  }
  const saved = savedTextPresentation(segment),
    original = text === segment.text ? Array.from(segment.text) : null,
    // Unchanged text keeps its saved CR/CRLF: map presentation positions back to it.
    source = (position) => {
      let first = 0,
        last = saved.offsets.length - 1;
      while (first < last) {
        const middle = (first + last) >> 1;
        if (saved.offsets[middle] < position) first = middle + 1;
        else last = middle;
      }
      return first;
    };
  return dirty.map((entry) => {
    const [start, end] = entry.values.map(wordTimeSeconds),
      left = original ? source(entry.start) : entry.start,
      right = original ? source(entry.end) : entry.end;
    return {
      char_start: left,
      char_end: right,
      word: original ? original.slice(left, right).join("") : entry.word,
      start,
      end,
    };
  });
}

(() => {
  const transcript = document.getElementById("transcript");
  const editorOf = (node) => {
    const editor = node?.closest?.(".passage-editor");
    const segment = editor ? segmentFromNode(editor) : null;
    return editor && segment ? { editor, segment } : null;
  };
  transcript.addEventListener("input", (event) => {
    const input = event.target;
    if (
      !input.matches?.("[data-word-time]") ||
      passageTextSaving ||
      timedWordSaving
    )
      return;
    const found = editorOf(input),
      record = found && wordTimeDrafts.get(found.editor),
      word = record?.current;
    if (!word || record.segment !== found.segment) return;
    if (!word.entry) {
      word.entry = {
        start: word.start,
        end: word.end,
        word: word.text,
        baseline: word.baseline,
        values: word.baseline.map(wordTimeText),
      };
      record.entries.push(word.entry);
      record.entries.sort((left, right) => left.start - right.start);
    }
    word.entry.values[input.dataset.wordTime === "end" ? 1 : 0] = input.value;
    record.layout = null;
    refreshPassageDraft(found.segment, found.editor);
  });
  // Follow the caret and selection; a text change seen here first also updates the draft.
  const follow = () => {
    const textarea = document.activeElement;
    if (!textarea?.matches?.(".passage-text-surface textarea")) return;
    const found = editorOf(textarea);
    if (!found || passageTextSaving || timedWordSaving) return;
    const record = wordTimeDrafts.get(found.editor);
    if (record?.segment === found.segment && record.draft !== textarea.value)
      refreshPassageDraft(found.segment, found.editor);
    else refreshWordTimes(found.segment, found.editor);
  };
  for (const type of ["select", "keyup", "mouseup", "focusin"])
    transcript.addEventListener(type, follow);
  document.addEventListener("selectionchange", follow);
  for (const type of ["compositionstart", "compositionend"])
    transcript.addEventListener(type, (event) => {
      const found = event.target.matches?.(".passage-text-surface textarea")
        ? editorOf(event.target)
        : null;
      if (!found) return;
      wordTimeRecord(found.segment, found.editor).composing =
        type === "compositionstart";
      refreshWordTimes(found.segment, found.editor);
    });
})();
