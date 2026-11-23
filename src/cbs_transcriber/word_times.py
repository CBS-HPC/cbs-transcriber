"""Typed word times saved with a passage's text as one version (ADR 0195).

Kept apart from transcripts.py, whose source is pinned by the portable semantic checker;
this module only composes that module's unchanged text policy and single-word timing rules.
"""

from bisect import bisect_right

from .transcripts import _complete_words, edit_manual_text_retaining_timing, edit_timed_word

MAX_TYPED_WORD_TIMES = 1000
_TYPED_TIME_MISMATCH = (
    "A word time you typed no longer matches one word in the passage. "
    "Save the text first, then set the time."
)


def edit_text_and_word_times(segment, text, word_times):
    """One Save passage (ADR 0195): literal text first (ADR 0166), then typed word times.

    Typed times name a word by its exact code-point range in the saved text; each range must
    overlap exactly one word of the resulting map. Every changed word is then checked with
    edit_timed_word's own rules against the final times of all other words, so a valid set
    of edits never fails because of the order it was typed in. Untyped words keep the text
    policy's outcome; no time is inferred. Returns (passage, warning, text_changed, changed).
    """
    if not isinstance(word_times, list) or len(word_times) > MAX_TYPED_WORD_TIMES:
        raise ValueError("Set at most 1,000 word times in one save.")
    edited, warning = edit_manual_text_retaining_timing(segment, text)
    text_changed = edited.get("text") != segment.get("text")
    if not word_times:
        return edited, warning, text_changed, []
    passage_text, words = edited.get("text"), edited.get("words")
    if not isinstance(passage_text, str) or not isinstance(words, list):
        raise ValueError("The saved word mapping no longer matches the passage text.")
    starts, ends = [], []
    for word in words:
        left = word.get("char_start") if isinstance(word, dict) else None
        right = word.get("char_end") if isinstance(word, dict) else None
        if (
            type(left) is not int
            or type(right) is not int
            or not (ends[-1] if ends else 0) <= left < right <= len(passage_text)
        ):
            raise ValueError("The saved word mapping no longer matches the passage text.")
        starts.append(left)
        ends.append(right)
    targets, previous = {}, 0
    for entry in sorted(word_times, key=lambda item: item["char_start"]):
        left, right = entry["char_start"], entry["char_end"]
        if (
            not previous <= left < right <= len(passage_text)
            or passage_text[left:right] != entry["word"]
        ):
            raise ValueError(_TYPED_TIME_MISMATCH)
        previous = right
        first = bisect_right(starts, left) - 1
        if first < 0 or ends[first] <= left:
            first += 1
        overlapping = []
        for index in range(first, len(words)):
            if starts[index] >= right or len(overlapping) > 1:
                break
            overlapping.append(index)
        if len(overlapping) != 1 or overlapping[0] in targets:
            raise ValueError(_TYPED_TIME_MISMATCH)
        targets[overlapping[0]] = (entry["start"], entry["end"])
    changed = []
    for index, (start, end) in targets.items():
        word = edited["words"][index]
        if (word.get("start"), word.get("end")) != (start, end):
            # Model alignment fit describes the original interval, not this manual one.
            word["manual_timing"] = True
            word.update(start=start, end=end)
            changed.append(index)
    if not changed:
        return edited, warning, text_changed, []
    edited["edited"] = True
    edited["words_current"] = True
    for index in changed:
        # The shared single-word rules: bounds, start before end, passage order, whole map.
        edit_timed_word(edited, index, *targets[index])
    if text_changed:
        edited["word_timing"] = _complete_words({**edited, "word_timing": True})
        if warning and all(
            word.get("start") is not None and word.get("end") is not None
            for word in edited["words"]
        ):
            warning = None
    return edited, warning, text_changed, changed
