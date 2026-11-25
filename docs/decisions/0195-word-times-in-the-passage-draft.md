# 0195 — Word times in the passage draft

Status: accepted for rc3 (user design of 20 and 21 November 2026). Replaces the passage
editor's ordinal word dropdown ("Word and timing", Edit word) for new edits. Builds on
ADR 0166 (timing-independent manual text saves), ADR 0156 (per-word timing edits),
ADR 0180 (Undo last change) and ADR 0159/0193 (manual edit hot paths).

## Decision

The passage editor is the ordinary Text editor plus two boxes, **Start (s)** and **End (s)**,
just below the textarea. They show the saved times of the word at the cursor and can be
edited in place. Typed times belong to the passage draft: **Save passage** sends the text and
the typed times in one request and records **one** History version, which Undo last change
restores as a whole; **Cancel** discards both. The textarea's value, selection, focus and
native Undo are only read, never written.

### Which word is at the cursor (settled conservatively while building)

- A caret strictly inside a word selects it.
- Between two words or on a space the boxes are empty and disabled (user, 21 November).
  This includes a caret touching a longer word only at its first or last letter.
- A **one-character word** (one grapheme, such as "i" or an emoji) has no inside, so a caret
  directly beside it selects it, unless the caret also touches another word. It is otherwise
  edited like any word.
- A selection selects a word only when its non-space part lies within that one word
  (double-click, Shift+Arrow). A selection spanning several words shows nothing.
- Positions are code points of the textarea's text (CRLF read as LF, as in the playback
  mirror). A caret inside a UTF-16 surrogate pair never selects a word.

### Which time the boxes show

- Unchanged saved words show their saved times; an untimed saved word shows empty boxes.
- In a changed draft, "unchanged" uses the existing provable prefix/suffix rule of Text
  playback highlighting. Every other whitespace-separated token is a new or changed word and
  starts empty, even where Save passage may later keep a replaced word's interval under
  ADR 0166. The boxes' tooltip explains that limit; the saved result shows the kept time.
- Typed times stay with their token while the text around it changes, including a spelling
  fix or punctuation inside or touching that token. Use the verified native replacement range
  when repeated text makes the changed occurrence ambiguous. Otherwise retain an interval only
  when the fallback positions agree. Deleting, splitting or losing proof of the token removes
  its typed times with a notice (B583). Native Undo of the text does not bring them back.
- During IME composition, keep previously typed intervals until the text settles.
  Block the combined Save while composition is active and the draft has typed times.
  This lets a transient split disappear without dropping a valid draft interval (B585).
- Nothing is inferred: a time appears in the result only where the saved word had one, the
  text policy kept one, or the user typed one.

### Wire and persistence

A new route keeps every older route unchanged:

`PATCH /api/recordings/{id}/segments/{segment_id}/edit-v4` with
`{revision, mode: "free", text, word_times: [{char_start, char_end, word, start, end}],
reason?, working_choice?}`; strict fields, 1–1,000 typed times. Ranges are code points of
the sent text; `word` must equal that text slice. The editor uses edit-v4 only when typed
times differ from what the boxes first showed; text-only saves keep edit-v3 (or edit-v1 with
the realignment opt-in).

`word_times.py` (kept out of `transcripts.py`, whose source the portable semantic checker
pins) applies the text with `edit_manual_text_retaining_timing` (ADR 0166), then names
each typed time's word as the one word of the resulting map overlapping its range; zero or
several words, overlapping or duplicate ranges, or a changed slice refuse the save. It sets
all typed times first and then checks every changed word with the unchanged
`edit_timed_word` rules (start before end, inside the passage bounds, passage order with all
other timed words, whole-map validity) against the final times, so a valid set never fails
because of the order it was typed in. Changed words get `manual_timing: true`. Require a
current word map after the text policy, including when only times changed (B582).

One revision-fenced `save_transcript` call records the version with the existing metadata:
a text change is `free_rewrite` with `text_policy: permissive-word-map-v2`; times alone are
`word_timing_edit` with `mode: timed_word` (and `word_index` when exactly one word changed).
No schema, history allowlist, Working, review or portable format changes. A text change
recomputes completeness with the same `_complete_words` check as ADR 0166 and drops its
"no verified timing" warning when every word now has a time; times alone keep the passage's
own completeness claim, as Edit word did. edit-v4 never queues realignment; with the opt-in
checked the editor says so.

### Unchanged behaviour

Working choice and its fences (the editor's choice covers edit-v4), late-reply and
navigation guards (typed times make the draft dirty), Undo/Redo, review marks, the dotted
uncertainty overlay and playback highlighting. The legacy `timed_word`/`mapped_word` modes
of the base route remain for older clients; the global Correct (word) mode is unchanged.

Fence an open timing draft by its recording context, result, revision, passage and saved
text. A same-revision metadata reply such as Rename can replace parsed objects without
invalidating the draft. A changed revision refuses Save and preserves the draft so it can
be recovered before reopening (B584).
Shown in the MVP profile; no feature flag.

## Limits

Real IME, screen-reader and Mac/Firefox acceptance remain open. Boxes for a changed word
cannot preview the interval ADR 0166 may keep on Save. Removing a saved time is not offered.
