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

function wordTimeSplitsSurrogate(text, position) {
  if (position <= 0 || position >= text.length) return false;
  const left = text.charCodeAt(position - 1),
    right = text.charCodeAt(position);
  return left >= 0xd800 && left <= 0xdbff && right >= 0xdc00 && right <= 0xdfff;
}

function wordTimeRecord(segment, editor) {
  let record = wordTimeDrafts.get(editor);
  if (!record) {
    record = {
      segment,
      owner: recordingContext(),
      revision: state.recording.revision,
      draft: editor.querySelector(".passage-text-surface textarea").value,
      entries: [],
      composing: false,
      layout: null,
      current: null,
      key: "",
      edit: null,
    };
    wordTimeDrafts.set(editor, record);
  }
  return record;
}

function wordTimeRecordMatches(record, segment) {
  if (!record) return false;
  const { epoch, id, result } = recordingContext();
  // Metadata replies can replace a parsed segment without changing the transcript.
  return (
    record.owner.epoch === epoch &&
    record.owner.id === id &&
    record.owner.result === result &&
    record.revision === state.recording.revision &&
    record.segment.id === segment.id &&
    record.segment.text === segment.text
  );
}

const wordTimeStaleMessage =
  "This passage changed while you were editing. Close and reopen the editor to see the new passage.";

function wordTimeEntryDirty(entry) {
  const [start, end] = entry.values.map(wordTimeSeconds);
  return (
    start === undefined ||
    end === undefined ||
    start !== entry.baseline[0] ||
    end !== entry.baseline[1]
  );
}

// Find the extremes of an edit consistent with the two strings. Repeated text can
// make a deletion or insertion fit at several positions.
function wordTimeEditRanges(before, after) {
  const limit = Math.min(before.length, after.length);
  let prefix = 0,
    suffix = 0;
  while (prefix < limit && before[prefix] === after[prefix]) prefix++;
  while (
    suffix < limit &&
    before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
  )
    suffix++;
  return [
    [Math.min(prefix, limit - suffix), before.length - suffix],
    [prefix, before.length - Math.min(suffix, limit - prefix)],
  ];
}

function wordTimeNativeRange(edit, first, last, shift, length) {
  if (!edit) return null;
  let { start, end } = edit;
  if (start === end && edit.type.startsWith("delete")) {
    if (edit.type.endsWith("Backward")) start += shift;
    else if (edit.type.endsWith("Forward")) end -= shift;
  }
  // Confirm that the observed replacement leaves the old prefix and suffix intact.
  return start >= 0 &&
    end <= length &&
    start <= end &&
    end - start + shift >= 0 &&
    start <= last[0] &&
    end >= first[1]
    ? [start, end]
    : null;
}

function wordTimeMove(entry, [head, tail], after, shift) {
  const { start, end } = entry;
  if (end < head || (end === head && wordTimeSeparated(after[end])))
    return [start, end];
  if (
    start > tail ||
    (start === tail && wordTimeSeparated(after[start + shift - 1]))
  )
    return [start + shift, end + shift];
  return start <= head && tail <= end ? [start, end + shift] : [start, start];
}

// Keep the occurrence identified by the native edit. For changes without an
// exact edit range, keep typed times only when both possible extremes agree.
function trackWordTimes(record, value) {
  if (value === record.draft) return;
  const edit = record.edit;
  record.edit = null;
  record.layout = null;
  if (!record.entries.length) {
    record.draft = value;
    return;
  }
  const before = Array.from(record.draft),
    after = Array.from(value),
    shift = after.length - before.length,
    [first, last] = wordTimeEditRanges(before, after),
    known = wordTimeNativeRange(edit, first, last, shift, before.length),
    kept = [];
  for (const entry of record.entries) {
    const [start, end] = wordTimeMove(entry, known ?? first, after, shift),
      [otherStart, otherEnd] = wordTimeMove(entry, known ?? last, after, shift),
      ambiguous = start !== otherStart || end !== otherEnd,
      word = after.slice(start, end).join(""),
      previous = kept[kept.length - 1];
    if (
      ambiguous ||
      end <= start ||
      /\s/u.test(word) ||
      (previous && previous.end > start)
    ) {
      if (wordTimeEntryDirty(entry))
        toast(
          ambiguous
            ? `The times you typed for “${entry.word}” were removed, because the edited word could not be matched safely.`
            : `The times you typed for “${entry.word}” were removed, because the word was deleted or split.`,
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
    [first, last] = same
      ? []
      : wordTimeEditRanges(saved.characters, characters),
    unchanged = same
      ? ranges
      : draftUnchangedRanges(segment, record.draft, ranges).words.filter(
          ([index]) =>
            ranges[index][2] <= first[0] || last[1] <= ranges[index][1],
        );
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
    { selectionStart: from, selectionEnd: to } = textarea;
  if (
    wordTimeSplitsSurrogate(value, from) ||
    wordTimeSplitsSurrogate(value, to)
  )
    return { reason: "between" };
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
    record = wordTimeRecord(segment, editor),
    current = wordTimeRecordMatches(record, segment);
  if (current) trackWordTimes(record, textarea.value);
  const dirty = record.entries.some(wordTimeEntryDirty);
  if (passageTextSaving || timedWordSaving) return dirty;
  const inputs = [...box.querySelectorAll("[data-word-time]")],
    status = box.querySelector("[data-word-times-status]");
  let word = null,
    problem = null,
    message = "";
  if (!current) message = wordTimeStaleMessage;
  else if (!wordTimeBounded(segment))
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
  if (!record) return [];
  if (!wordTimeRecordMatches(record, segment))
    throw new Error(wordTimeStaleMessage);
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
  transcript.addEventListener("beforeinput", (event) => {
    const input = event.target;
    if (!input.matches?.(".passage-text-surface textarea")) return;
    const found = editorOf(input),
      record = found && wordTimeDrafts.get(found.editor);
    if (
      !record?.entries.length ||
      !wordTimeRecordMatches(record, found.segment)
    )
      return;
    record.edit = null;
    const { value, selectionStart, selectionEnd } = input,
      type = event.inputType;
    if (
      event.isComposing ||
      record.draft !== value ||
      !/^(?:insertText|insertFromPaste|insertLineBreak|insertParagraph|deleteByCut|deleteContent|delete.*(?:Backward|Forward))$/u.test(
        type,
      ) ||
      wordTimeSplitsSurrogate(value, selectionStart) ||
      wordTimeSplitsSurrogate(value, selectionEnd)
    )
      return;
    const edit = {
      start: wordTimeCodePoints(value.slice(0, selectionStart)),
      end: wordTimeCodePoints(value.slice(0, selectionEnd)),
      type,
    };
    record.edit = edit;
    setTimeout(() => {
      if (record.edit === edit) record.edit = null;
    }, 0);
  });
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
    if (!word || !wordTimeRecordMatches(record, found.segment)) return;
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
    if (
      wordTimeRecordMatches(record, found.segment) &&
      record.draft !== textarea.value
    )
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
