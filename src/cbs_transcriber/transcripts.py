"""Pure transcript import, editing, and export functions."""

import ast
import copy
import csv
import hashlib
import html
import io
import json
import math
import re
import uuid
from array import array
from bisect import bisect_left, bisect_right
from datetime import date
from pathlib import Path
from typing import Literal

MAX_TRANSCRIPT_BYTES = 32 * 1024 * 1024
ExportFormat = Literal["text", "nvivo-windows", "nvivo-mac", "srt", "vtt", "csv", "json", "docx"]


def timestamp(value):
    if value is None or value == "":
        return None
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Timestamps must be finite, nonnegative seconds.") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError("Timestamps must be finite, nonnegative seconds.")
    return number


def _interval(item):
    start, end = timestamp(item.get("start")), timestamp(item.get("end"))
    if start is not None and end is not None and end < start:
        raise ValueError("A transcript interval ends before it starts.")
    return start, end


def _word_data(value):
    if isinstance(value, str):
        if not value.strip():
            return []
        if len(value) > 2_000_000:
            raise ValueError("A CSV words field is too large.")
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            # Transcriber CSV can contain Python repr lists. Reject calls/deep trees.
            tree = ast.parse(value, mode="eval")
            nodes = list(ast.walk(tree))
            if len(nodes) > 100_000:
                raise ValueError("A CSV words field is too complex.") from None
            value = ast.literal_eval(tree)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(w, dict) for w in value):
        raise ValueError("Words must be a list of word objects.")
    return value


HINT_FIELDS = ("avg_logprob", "no_speech_prob", "compression_ratio")
MAX_TOKEN_LOGPROBS = 500_000


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def token_word_confidences(text, logprobs):
    """Map token log probabilities onto whitespace words through exact UTF-8 byte offsets.

    Returns [char_start, char_end, probability] per word, using the least likely token
    overlapping the word. Any mismatch with the transcript text yields no hints at all.
    """
    if not isinstance(logprobs, list) or not logprobs or len(logprobs) > MAX_TOKEN_LOGPROBS:
        return []
    data, tokens = bytearray(), []
    for entry in logprobs:
        if not isinstance(entry, dict):
            return []
        raw, logprob = entry.get("bytes"), _finite(entry.get("logprob"))
        if isinstance(raw, list) and all(isinstance(b, int) and 0 <= b <= 255 for b in raw):
            chunk = bytes(raw)
        elif isinstance(entry.get("token"), str):
            chunk = entry["token"].encode("utf-8")
        else:
            return []
        if logprob is None or logprob > 0:
            return []
        tokens.append((len(data), len(data) + len(chunk), logprob))
        data.extend(chunk)
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    if decoded.strip() != text.strip():
        return []
    # Character offset in `text` -> byte offset in the decoded token stream.
    shift = (len(decoded) - len(decoded.lstrip())) - (len(text) - len(text.lstrip()))
    byte_at = [0]
    for character in decoded:
        byte_at.append(byte_at[-1] + len(character.encode("utf-8")))
    result, cursor = [], 0
    for match in re.finditer(r"\S+", text):
        first, last = byte_at[match.start() + shift], byte_at[match.end() + shift]
        while cursor < len(tokens) and tokens[cursor][1] <= first:
            cursor += 1
        lowest, index = 0.0, cursor
        while index < len(tokens) and tokens[index][0] < last:
            lowest = min(lowest, tokens[index][2])
            index += 1
        if index > cursor:
            result.append([match.start(), match.end(), round(math.exp(lowest), 4)])
    return result


def _segment_hints(row, text, confidences, kind):
    hints = {}
    for field in HINT_FIELDS:
        value = _finite(row.get(field))
        if value is not None:
            hints[field] = round(value, 4)
    if not confidences and row.get("_token_logprobs") is not None:
        confidences, kind = token_word_confidences(text, row["_token_logprobs"]), "transcription"
    if confidences:
        hints["words"] = confidences
        hints["word_kind"] = kind
    return hints


LEGACY_ENHANCEMENT_NAME = re.compile(r"ENH_[A-Za-z0-9]+_(\d{1,4})")


def _speaker_number(label):
    """1-based ordinal from a raw diarization label, or None for free-form labels."""
    if match := re.fullmatch(r"SPEAKER_(\d{1,4})", label):
        return int(match.group(1)) + 1  # pyannote/WhisperX count from zero
    if match := LEGACY_ENHANCEMENT_NAME.fullmatch(label):
        return int(match.group(1))
    if re.fullmatch(r"[A-Z]", label):
        return ord(label) - ord("A") + 1  # OpenAI diarization letters
    if re.fullmatch(r"\d{1,4}", label):
        return int(label) + 1
    return None


def saved_speaker_names(speakers):
    """Researcher-chosen names; earlier app-generated "Speaker N" defaults are not choices."""
    return {
        raw: name
        for raw, name in (speakers or {}).items()
        if not (
            (match := LEGACY_ENHANCEMENT_NAME.fullmatch(raw))
            and name == f"Speaker {int(match.group(1))}"
        )
    }


def default_speaker_names(segments, speakers):
    """Display names for unnamed raw labels: Speaker_01, Speaker_02, ... like Transcriber.

    Derived from the raw label itself, so names stay stable when passages are edited.
    Passages keep their raw labels; saved names always win.
    """
    saved = saved_speaker_names(speakers)
    names = {}
    for segment in segments:
        raw = segment.get("speaker") if isinstance(segment, dict) else None
        if not isinstance(raw, str) or raw in names or raw in saved:
            continue
        if raw == "UNKNOWN":
            names[raw] = "Unknown speaker"
            continue
        prefix, label = "", raw
        if part := re.fullmatch(r"(Part \d+ · )(.+)", raw):
            prefix, label = part.groups()
        number = _speaker_number(label)
        names[raw] = prefix + f"Speaker_{number:02d}" if number else raw
    # Distinct raw labels must never look like the same voice.
    shown = {}
    for raw, name in names.items():
        shown.setdefault(name, []).append(raw)
    taken = set(saved.values())
    for name, raws in shown.items():
        if len(raws) > 1 or name in taken:
            for raw in raws:
                if names[raw] != raw:
                    names[raw] = f"{name} ({raw})"
    return names


def resolved_speakers(segments, speakers):
    return {**default_speaker_names(segments, speakers), **saved_speaker_names(speakers)}


def pop_hints(segments):
    """Remove review hints from passages before saving; returns them keyed by passage ID."""
    return {
        segment["id"]: hints
        for segment in segments
        if isinstance(segment, dict) and (hints := segment.pop("_hints", None))
    }


def hints_document(hints, segments, origin):
    """Bind each passage's hints to its exact text so edited passages never show them."""
    if not hints:
        return None
    texts = {segment["id"]: segment["text"] for segment in segments if isinstance(segment, dict)}
    return {
        "version": 1,
        "origin": origin,
        "passages": {
            identifier: value
            | {"text_sha256": hashlib.sha256(texts[identifier].encode("utf-8")).hexdigest()}
            for identifier, value in hints.items()
            if identifier in texts
        },
    }


def slice_hints(hints, begin, finish):
    """Keep passage metrics and the word hints inside one fragment of a split passage."""
    if not hints:
        return None
    part = {key: value for key, value in hints.items() if key not in {"words"}}
    words = [
        [start - begin, end - begin, value]
        for start, end, value in hints.get("words", [])
        if start >= begin and end <= finish
    ]
    if words:
        part["words"] = words
    else:
        part.pop("word_kind", None)
    return part if set(part) - {"word_kind"} else None


def _index_words(words):
    return sorted(
        ((timestamp(word["start"]) + timestamp(word["end"])) / 2, index, word)
        for index, word in enumerate(words)
        if word.get("start") is not None and word.get("end") is not None
    )


def _interval_words(indexed, start, end):
    selected = indexed[bisect_left(indexed, (start,)) : bisect_left(indexed, (end,))]
    # Normalization follows the supplied lexical order, even when times are unordered.
    return [word for _, _, word in sorted(selected, key=lambda entry: entry[1])]


def normalize_segments(rows, top_words=None):
    if not isinstance(rows, list) or not rows:
        raise ValueError("The transcript contains no segments.")
    top_words = _word_data(top_words)
    indexed_words = None
    result = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            raise ValueError("Every segment must contain text.")
        start, end = _interval(row)
        text = row["text"]
        words = _word_data(row.get("words"))
        if not words and top_words and start is not None and end is not None:
            if indexed_words is None:
                indexed_words = _index_words(top_words)
            words = _interval_words(indexed_words, start, end)
        normalized, cursor, reliable = [], 0, bool(words)
        confidences, kind = [], None
        previous_start, previous_end = -1, -1
        for word in words:
            token = word.get("word", word.get("text", ""))
            if not isinstance(token, str) or not token.strip():
                reliable = False
                continue
            ws, we = _interval(word)
            # Character offsets keep exact punctuation and whitespace during corrections.
            match = re.search(re.escape(token.strip()), text[cursor:], re.IGNORECASE)
            if not match:
                reliable = False
                continue
            left, right = cursor + match.start(), cursor + match.end()
            normalized.append(
                {
                    "word": text[left:right],
                    "start": ws,
                    "end": we,
                    "char_start": left,
                    "char_end": right,
                }
            )
            cursor = right
            # WhisperX "score" is alignment fit; faster-whisper "probability" is decoding.
            confidence = _finite(word.get("score", word.get("probability")))
            if confidence is not None and 0 <= confidence <= 1:
                confidences.append([left, right, round(confidence, 4)])
                kind = "alignment" if "score" in word else "transcription"
            if ws is None or we is None:
                reliable = False
            else:
                if (
                    we <= ws
                    or ws < previous_start
                    or we < previous_end
                    or (start is not None and ws < start)
                    or (end is not None and we > end)
                ):
                    reliable = False
                previous_start, previous_end = ws, we
        # Unmapped lexical material means timing is partial; use segment highlighting.
        uncovered = text
        for word in reversed(normalized):
            uncovered = uncovered[: word["char_start"]] + uncovered[word["char_end"] :]
        reliable = reliable and not re.search(r"\w", uncovered)
        speaker = row.get("speaker") or "UNKNOWN"
        if not isinstance(speaker, (str, int)):
            raise ValueError("Speaker labels must be text.")
        segment = {
            "id": "",
            "start": start,
            "end": end,
            "speaker": str(speaker),
            "text": text,
            "original_text": text,
            "words": normalized,
            "original_words": copy.deepcopy(normalized),
            "word_timing": bool(reliable),
            "original_word_timing": bool(reliable),
            "edited": False,
        }
        # Review hints travel beside the passage and are stored outside its text versions.
        hints = _segment_hints(row, text, confidences, kind)
        if hints:
            segment["_hints"] = hints
        result.append(segment)
    result.sort(key=lambda s: s["start"] if s["start"] is not None else float("inf"))
    for index, segment in enumerate(result):
        segment["id"] = str(index)
    return result


def parse_transcript(data: bytes, filename: str, *, generation=1):
    # Receipt schema selects these frozen interpretations. Existing producer callers and
    # schema1 receipts keep generation1; fresh user imports explicitly select generation2.
    if type(generation) is not int or generation not in (1, 2):
        raise ValueError("The transcript parser generation is unsupported.")
    if len(data) > MAX_TRANSCRIPT_BYTES:
        raise ValueError("Transcript files must be 32 MB or smaller.")
    try:
        text = data.decode("utf-8-sig")
        if Path(filename).suffix.lower() == ".csv":
            csv.field_size_limit(MAX_TRANSCRIPT_BYTES)
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=generation == 2)
            if not reader.fieldnames or not {"start", "end", "text"} <= set(reader.fieldnames):
                raise ValueError("CSV needs start, end, and text columns.")
            if generation == 2 and (
                len(reader.fieldnames) != len(set(reader.fieldnames))
                or any(not name for name in reader.fieldnames)
            ):
                raise ValueError("CSV column names must be unique and nonempty.")
            rows, top_words = list(reader), None
            if generation == 2 and any(
                None in row or any(value is None for value in row.values()) for row in rows
            ):
                raise ValueError("Every CSV row must match its header columns.")
        elif Path(filename).suffix.lower() == ".json":
            payload = json.loads(text)
            if isinstance(payload, list):
                rows, top_words = payload, None
            elif isinstance(payload, dict):
                rows, top_words = payload.get("segments"), payload.get("words")
                if not rows and isinstance(payload.get("text"), str):
                    rows = [{"text": payload["text"]}]
            else:
                raise ValueError("JSON must contain a transcript object or segment list.")
        else:
            raise ValueError("Import a JSON or CSV transcript.")
        if generation == 2 and isinstance(rows, list):
            rows = [
                {**row, "speaker": "0"}
                if isinstance(row, dict) and type(row.get("speaker")) is int and row["speaker"] == 0
                else row
                for row in rows
            ]
        return normalize_segments(rows, top_words)
    except (UnicodeError, SyntaxError, TypeError, KeyError, RecursionError, csv.Error) as exc:
        raise ValueError("The transcript has an invalid encoding or structure.") from exc


def edit_segment(segment, mode, text=None, word_index=None):
    edited = copy.deepcopy(segment)
    if mode == "restore":
        edited.update(
            text=edited["original_text"],
            words=copy.deepcopy(edited["original_words"]),
            word_timing=edited["original_word_timing"],
            edited=False,
        )
        edited.pop("words_current", None)
    elif mode == "free":
        if not isinstance(text, str) or len(text) > 200_000:
            raise ValueError("Supply passage text of at most 200,000 characters.")
        if text != edited["text"]:
            edited.update(text=text, word_timing=False, edited=True)
            edited.pop("words_current", None)
    elif mode == "word":
        if not edited["word_timing"]:
            raise ValueError("This passage has no valid word timing. Restore it or use free text.")
        if not isinstance(text, str) or not text.strip() or re.search(r"\s", text.strip()):
            raise ValueError("Replace one word only; use free text to insert or delete words.")
        if word_index is None or not 0 <= word_index < len(edited["words"]):
            raise ValueError("Choose an existing word.")
        word = edited["words"][word_index]
        left, right = word["char_start"], word["char_end"]
        replacement = text.strip()
        edited["text"] = edited["text"][:left] + replacement + edited["text"][right:]
        delta = len(replacement) - (right - left)
        word.update(word=replacement, char_end=left + len(replacement))
        for following in edited["words"][word_index + 1 :]:
            following["char_start"] += delta
            following["char_end"] += delta
        edited["edited"] = True
    else:
        raise ValueError("Unknown editing mode.")
    return edited


TEXT_TIMING_POLICY = "conservative-word-map-v1"
MAX_TEXT_EDIT_CELLS = 250_000
_LEXICAL_TOKEN = re.compile(r"\w+(?:['’\-]\w+)*", re.UNICODE)
_TIMING_REFUSAL = (
    "This text change cannot retain the existing word timing unambiguously. "
    "Make smaller text edits, or explicitly choose realignment when available."
)


def _text_edit_pairs(before, after):
    """Return only token correspondences shared by every minimum-cost edit path."""
    if not after:
        return []
    if not before:
        return [-1] * len(after)
    if before == after:
        return list(range(len(before)))
    prefix = 0
    while prefix < min(len(before), len(after)) and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < min(len(before), len(after)) - prefix and before[-1 - suffix] == after[-1 - suffix]
    ):
        suffix += 1
    left, right = len(before) - suffix, len(after) - suffix
    middle = set(before[prefix:left]) | set(after[prefix:right])
    outside = set(before[:prefix]) | set(before[left:])
    # Exact outer runs are safe anchors only when none of their lexical keys can
    # instead belong to the changed middle (notably repeated-word insert/delete).
    if outside & middle:
        prefix, suffix, left, right = 0, 0, len(before), len(after)
    old, new = before[prefix:left], after[prefix:right]
    rows, columns = len(old), len(new)
    if (rows + 1) * (columns + 1) > MAX_TEXT_EDIT_CELLS:
        raise ValueError(_TIMING_REFUSAL)
    forward = [array("I", range(columns + 1))]
    for i, token in enumerate(old, 1):
        previous, current = forward[-1], array("I", [i])
        for j, other in enumerate(new, 1):
            current.append(
                min(previous[j] + 1, current[-1] + 1, previous[j - 1] + (token != other))
            )
        forward.append(current)
    optimum, candidates = forward[-1][-1], [-3] * columns

    def observe(column, candidate):
        if candidates[column] == -3:
            candidates[column] = candidate
        elif candidates[column] != candidate:
            candidates[column] = -2

    following = None
    for i in range(rows, -1, -1):
        current = array("I", [0]) * (columns + 1)
        current[columns] = rows - i
        for j in range(columns - 1, -1, -1):
            if i == rows:
                current[j] = columns - j
            else:
                current[j] = min(
                    following[j] + 1,
                    current[j + 1] + 1,
                    following[j + 1] + (old[i] != new[j]),
                )
                if forward[i][j] + (old[i] != new[j]) + following[j + 1] == optimum:
                    observe(j, i)
            if forward[i][j] + 1 + current[j + 1] == optimum:
                observe(j, -1)
        following = current
    return (
        list(range(prefix))
        + [value + prefix if value >= 0 else value for value in candidates]
        + list(range(left, len(before)))
    )


def edit_text_retaining_timing(segment, text):
    """Fresh Text-save policy: retain proved intervals, never infer inserted timing."""
    if not isinstance(text, str) or len(text) > 200_000:
        raise ValueError("Supply passage text of at most 200,000 characters.")
    if text == segment.get("text"):
        return copy.deepcopy(segment), None
    old_text, source_words = segment.get("text"), segment.get("words", [])
    if not isinstance(old_text, str) or not isinstance(source_words, list):
        raise ValueError("The saved word mapping is invalid.")
    old_tokens, new_tokens = list(re.finditer(r"\S+", old_text)), list(re.finditer(r"\S+", text))

    def key(token):
        return tuple(_LEXICAL_TOKEN.findall(token.group().casefold())) or (token.group(),)

    old_keys, new_keys = list(map(key, old_tokens)), list(map(key, new_tokens))
    groups = [[] for _ in old_tokens]
    usable = word_mapping_current(segment)
    if usable:
        if type(segment.get("word_timing", False)) is not bool or (
            "words_current" in segment and type(segment["words_current"]) is not bool
        ):
            raise ValueError("The saved word timing state is invalid.")
        passage_start, passage_end = _interval(segment)
        previous, previous_start, previous_end, owner = 0, -1, -1, 0
        for word in source_words:
            if not isinstance(word, dict):
                raise ValueError("The saved word mapping is invalid.")
            left, right = word.get("char_start"), word.get("char_end")
            if (
                type(left) is not int
                or type(right) is not int
                or not previous <= left < right <= len(old_text)
                or old_text[left:right] != word.get("word")
                or any(
                    field in word and word[field] is not True
                    for field in ("manual_timing", "alignment_hints_stale")
                )
            ):
                raise ValueError("The saved word mapping no longer matches the passage text.")
            try:
                finite = all(
                    value is None
                    or (type(value) in (int, float) and math.isfinite(value) and value >= 0)
                    for value in (word.get("start"), word.get("end"))
                )
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError("The saved word timing is invalid.")
            ws, we = _interval(word)
            if (
                (ws is not None and ws < previous_start)
                or (we is not None and we < previous_end)
                or (ws is not None and we is not None and ws >= we)
                or any(
                    value is not None
                    and (
                        (passage_start is not None and value < passage_start)
                        or (passage_end is not None and value > passage_end)
                    )
                    for value in (ws, we)
                )
            ):
                raise ValueError("The saved word timing is inconsistent with the passage.")
            if ws is not None:
                previous_start = ws
            if we is not None:
                previous_end = we
            previous = right
            while owner < len(old_tokens) and old_tokens[owner].end() <= left:
                owner += 1
            if owner == len(old_tokens) or not (
                old_tokens[owner].start() <= left < right <= old_tokens[owner].end()
            ):
                raise ValueError(_TIMING_REFUSAL)
            groups[owner].append(word)
    has_timing = usable and any(
        word.get("start") is not None or word.get("end") is not None for word in source_words
    )
    try:
        pairs = (
            _text_edit_pairs(old_keys, new_keys)
            if usable and source_words
            else [-1] * len(new_tokens)
        )
    except ValueError:
        if has_timing:
            raise
        pairs = [-1] * len(new_tokens)  # No interval exists to discard or accidentally lend.
    old_set, words = set(old_keys), []

    def untimed(left, right):
        words.append(
            {
                "word": text[left:right],
                "start": None,
                "end": None,
                "char_start": left,
                "char_end": right,
            }
        )

    for new_index, (token, source_index) in enumerate(zip(new_tokens, pairs, strict=True)):
        if source_index == -2:
            if has_timing:
                raise ValueError(_TIMING_REFUSAL)
            untimed(token.start(), token.end())
            continue
        if source_index < 0 or not groups[source_index]:
            untimed(token.start(), token.end())
            continue
        source, original = old_tokens[source_index], groups[source_index]
        if token.group() == source.group():
            offset, cursor = token.start() - source.start(), token.start()
            for word in original:
                left, right = word["char_start"] + offset, word["char_end"] + offset
                if re.search(r"\w", text[cursor:left]):
                    untimed(cursor, left)
                words.append({**copy.deepcopy(word), "char_start": left, "char_end": right})
                cursor = right
            if re.search(r"\w", text[cursor : token.end()]):
                untimed(cursor, token.end())
        elif (
            len(original) == 1
            and len(_LEXICAL_TOKEN.findall(source.group())) == 1
            and len(_LEXICAL_TOKEN.findall(token.group())) == 1
            and not re.search(
                r"\w",
                old_text[source.start() : original[0]["char_start"]]
                + old_text[original[0]["char_end"] : source.end()],
            )
            and (
                old_keys[source_index] == new_keys[new_index] or new_keys[new_index] not in old_set
            )
        ):
            words.append(
                {
                    **copy.deepcopy(original[0]),
                    "word": token.group(),
                    "char_start": token.start(),
                    "char_end": token.end(),
                    "alignment_hints_stale": True,
                }
            )
        else:
            if any(
                word.get("start") is not None or word.get("end") is not None for word in original
            ):
                raise ValueError(_TIMING_REFUSAL)
            untimed(token.start(), token.end())
    edited = copy.deepcopy(segment)
    edited.update(text=text, words=words, edited=True, words_current=True)
    # Unlike structural splitting, Text saves never derive new passage bounds.
    edited["word_timing"] = _complete_words({**edited, "word_timing": True})
    warning = None
    if not usable:
        warning = "The previous word mapping was stale. The saved text remains untimed."
    return edited, warning


def word_mapping_current(segment):
    """Distinguish usable partial maps from words invalidated by free rewrites."""
    return bool(
        segment.get("word_timing")
        or not segment.get("edited")
        or segment.get("words_current") is True
    )


MANUAL_TEXT_TIMING_POLICY = "permissive-word-map-v2"
MANUAL_SPLIT_POLICY = "manual-word-boundary-v2"


def _manual_interval(item):
    """Return only already valid numeric bounds, without coercing saved data."""
    start, end = item.get("start"), item.get("end")
    try:
        if any(
            value is not None
            and (type(value) not in (int, float) or not math.isfinite(value) or value < 0)
            for value in (start, end)
        ) or (start is not None and end is not None and start >= end):
            return None
    except OverflowError:
        return None
    return start, end


def _manual_word_groups(segment, tokens):
    """Keep independently verified words; uncertain source regions grant no timing."""
    groups = [[] for _ in tokens]
    text, words = segment.get("text"), segment.get("words", [])
    bounds = _manual_interval(segment)
    if (
        not isinstance(text, str)
        or not isinstance(words, list)
        or len(words) > 200_000
        or not word_mapping_current(segment)
        or type(segment.get("word_timing", False)) is not bool
        or ("words_current" in segment and type(segment["words_current"]) is not bool)
        or bounds is None
    ):
        return groups, True
    starts, invalid, dropped = [token.start() for token in tokens], set(), False
    previous_char, previous_start, previous_end = None, None, None
    for word in words:
        if not isinstance(word, dict):
            dropped = True
            continue
        left, right = word.get("char_start"), word.get("char_end")
        if (
            type(left) is not int
            or type(right) is not int
            or not 0 <= left < right <= len(text)
            or text[left:right] != word.get("word")
        ):
            dropped = True
            continue
        owner = bisect_right(starts, left) - 1
        if owner < 0 or right > tokens[owner].end():
            # A group such as "New York" does not locate either individual word.
            dropped = True
            continue
        interval = _manual_interval(word)
        if (
            interval is None
            or any(
                field in word and word[field] is not True
                for field in ("manual_timing", "alignment_hints_stale")
            )
            or any(
                value is not None
                and (
                    (bounds[0] is not None and value < bounds[0])
                    or (bounds[1] is not None and value > bounds[1])
                )
                for value in interval or ()
            )
        ):
            invalid.add(owner)
            continue
        ws, we = interval
        if previous_char is not None and left < previous_char[0]:
            invalid.update((owner, previous_char[1]))
        if ws is not None and previous_start is not None and ws < previous_start[0]:
            invalid.update((owner, previous_start[1]))
        if we is not None and previous_end is not None and we < previous_end[0]:
            invalid.update((owner, previous_end[1]))
        previous_char = right, owner
        if ws is not None:
            previous_start = ws, owner
        if we is not None:
            previous_end = we, owner
        groups[owner].append(word)
    for owner in invalid:
        groups[owner] = []
    return groups, dropped or bool(invalid)


def _manual_untimed(text, left, right):
    return {
        "word": text[left:right],
        "start": None,
        "end": None,
        "char_start": left,
        "char_end": right,
    }


def _manual_token_words(text, token, original, offset=0):
    """Retain an exact token's verified pieces and mark its uncovered text untimed."""
    if not original:
        return [_manual_untimed(text, token.start(), token.end())]
    words, cursor = [], token.start()
    for word in original:
        left, right = word["char_start"] + offset, word["char_end"] + offset
        if re.search(r"\w", text[cursor:left]):
            words.append(_manual_untimed(text, cursor, left))
        words.append({**copy.deepcopy(word), "char_start": left, "char_end": right})
        cursor = right
    if re.search(r"\w", text[cursor : token.end()]):
        words.append(_manual_untimed(text, cursor, token.end()))
    return words


def edit_manual_text_retaining_timing(segment, text):
    """Save literal wording; uncertain correspondence clears timing, never blocks it."""
    if not isinstance(text, str) or len(text) > 200_000:
        raise ValueError("Supply passage text of at most 200,000 characters.")
    if text == segment.get("text"):
        return copy.deepcopy(segment), None
    old_text = segment.get("text")
    old_tokens = list(re.finditer(r"\S+", old_text)) if isinstance(old_text, str) else []
    new_tokens = list(re.finditer(r"\S+", text))
    groups, uncertain = _manual_word_groups(segment, old_tokens)

    def key(token):
        return tuple(_LEXICAL_TOKEN.findall(token.group().casefold())) or (token.group(),)

    old_keys, new_keys = list(map(key, old_tokens)), list(map(key, new_tokens))
    if (
        isinstance(old_text, str)
        and text.startswith(old_text)
        and len(new_tokens) >= len(old_tokens)
        and all(
            old.span() == new.span() and old.group() == new.group()
            for old, new in zip(old_tokens, new_tokens, strict=False)
        )
    ):
        # Literal appends identify the old prefix even when its final word repeats.
        pairs = list(range(len(old_tokens))) + [-1] * (len(new_tokens) - len(old_tokens))
    else:
        try:
            pairs = _text_edit_pairs(old_keys, new_keys)
        except ValueError:
            pairs, uncertain = [-1] * len(new_tokens), True
    old_set, words = set(old_keys), []
    for new_index, (token, source_index) in enumerate(zip(new_tokens, pairs, strict=True)):
        if source_index < 0 or not groups[source_index]:
            words.append(_manual_untimed(text, token.start(), token.end()))
            uncertain = True
            continue
        source, original = old_tokens[source_index], groups[source_index]
        if token.group() == source.group():
            words.extend(_manual_token_words(text, token, original, token.start() - source.start()))
        elif (
            len(original) == 1
            and len(_LEXICAL_TOKEN.findall(source.group())) == 1
            and len(_LEXICAL_TOKEN.findall(token.group())) == 1
            and not re.search(
                r"\w",
                old_text[source.start() : original[0]["char_start"]]
                + old_text[original[0]["char_end"] : source.end()],
            )
            and (
                old_keys[source_index] == new_keys[new_index] or new_keys[new_index] not in old_set
            )
        ):
            words.append(
                {
                    **copy.deepcopy(original[0]),
                    "word": token.group(),
                    "char_start": token.start(),
                    "char_end": token.end(),
                    "alignment_hints_stale": True,
                }
            )
        else:
            words.append(_manual_untimed(text, token.start(), token.end()))
            uncertain = True
    edited = copy.deepcopy(segment)
    edited.update(text=text, words=words, words_current=True, edited=True)
    edited["word_timing"] = _complete_words({**edited, "word_timing": True})
    warning = None
    if words and (
        uncertain or any(word.get("start") is None or word.get("end") is None for word in words)
    ):
        warning = (
            "Some words have no verified timing after this edit. "
            "Only identifiable existing words retain their timing."
        )
    return edited, warning


def edit_mapped_word(segment, word_index, text):
    """Correct one word in a current map without modifying any audio timing."""
    if not word_mapping_current(segment):
        raise ValueError("The saved word mapping is stale. Restore or add word timing first.")
    if type(segment.get("word_timing", False)) is not bool or (
        "words_current" in segment and type(segment["words_current"]) is not bool
    ):
        raise ValueError("The saved word timing state is invalid.")
    words, passage_text = segment.get("words"), segment.get("text")
    if (
        type(word_index) is not int
        or not isinstance(words, list)
        or not isinstance(passage_text, str)
        or not 0 <= word_index < len(words)
    ):
        raise ValueError("Choose an existing mapped word.")
    if not isinstance(text, str) or not text.strip() or re.search(r"\s", text.strip()):
        raise ValueError("Replace one word only; use free text to insert or delete words.")
    previous_char = 0
    for word in words:
        if not isinstance(word, dict):
            raise ValueError("The saved word mapping is invalid.")
        left, right = word.get("char_start"), word.get("char_end")
        if (
            type(left) is not int
            or type(right) is not int
            or not previous_char <= left < right <= len(passage_text)
            or passage_text[left:right] != word.get("word")
        ):
            raise ValueError("The saved word mapping no longer matches the passage text.")
        if any(
            key in word and word[key] is not True
            for key in ("manual_timing", "alignment_hints_stale")
        ):
            raise ValueError("The saved word timing state is invalid.")
        previous_char = right
    edited = copy.deepcopy(segment)
    word = edited["words"][word_index]
    left, right = word["char_start"], word["char_end"]
    replacement = text.strip()
    if len(passage_text) - (right - left) + len(replacement) > 200_000:
        raise ValueError("Passage text must be at most 200,000 characters.")
    edited["text"] = passage_text[:left] + replacement + passage_text[right:]
    delta = len(replacement) - (right - left)
    word.update(word=replacement, char_end=left + len(replacement))
    for following in edited["words"][word_index + 1 :]:
        following["char_start"] += delta
        following["char_end"] += delta
    if edited != segment:
        edited["edited"] = True
        edited["words_current"] = True
    return edited


def edit_timed_word(segment, word_index, start, end, text=None):
    """Manually adjust one mapped word without inferring any other audio timing."""
    try:
        finite = all(type(value) in (int, float) and math.isfinite(value) for value in (start, end))
    except OverflowError:
        finite = False
    if type(word_index) is not int or not finite or start < 0 or start >= end:
        raise ValueError("Choose a word and finite start and end times with start before end.")
    passage_start, passage_end = _interval(segment)
    if passage_start is None or passage_end is None or passage_start >= passage_end:
        raise ValueError("This passage needs known audio bounds before changing word timing.")
    if start < passage_start or end > passage_end:
        raise ValueError("Word timing must stay within the passage audio bounds.")
    if not word_mapping_current(segment):
        raise ValueError("The saved word mapping is stale. Restore or add word timing first.")
    if type(segment.get("word_timing", False)) is not bool or (
        "words_current" in segment and type(segment["words_current"]) is not bool
    ):
        raise ValueError("The saved word timing state is invalid.")
    words, passage_text = segment.get("words"), segment.get("text")
    if (
        not isinstance(words, list)
        or not isinstance(passage_text, str)
        or not 0 <= word_index < len(words)
    ):
        raise ValueError("Choose an existing mapped word.")
    previous_char = 0
    for index, word in enumerate(words):
        if not isinstance(word, dict):
            raise ValueError("The saved word mapping is invalid.")
        left, right = word.get("char_start"), word.get("char_end")
        if (
            type(left) is not int
            or type(right) is not int
            or not previous_char <= left < right <= len(passage_text)
            or passage_text[left:right] != word.get("word")
        ):
            raise ValueError("The saved word mapping no longer matches the passage text.")
        previous_char = right
        if index == word_index:
            continue
        ws, we = _interval(word)
        if index < word_index and (
            (ws is not None and ws > start) or (we is not None and we > end)
        ):
            raise ValueError("Word times must remain in passage order.")
        if index > word_index and (
            (ws is not None and ws < start) or (we is not None and we < end)
        ):
            raise ValueError("Word times must remain in passage order.")
    if text is not None and (
        not isinstance(text, str) or not text.strip() or re.search(r"\s", text.strip())
    ):
        raise ValueError("Replace one word only; use free text to insert or delete words.")
    edited = copy.deepcopy(segment)
    word = edited["words"][word_index]
    left, right = word["char_start"], word["char_end"]
    replacement = word["word"] if text is None else text.strip()
    if len(passage_text) - (right - left) + len(replacement) > 200_000:
        raise ValueError("Passage text must be at most 200,000 characters.")
    edited["text"] = passage_text[:left] + replacement + passage_text[right:]
    delta = len(replacement) - (right - left)
    word.update(word=replacement, char_end=left + len(replacement))
    for following in edited["words"][word_index + 1 :]:
        following["char_start"] += delta
        following["char_end"] += delta
    if (word.get("start"), word.get("end")) != (start, end):
        # Model alignment fit describes the original interval, not this manual one.
        word["manual_timing"] = True
        word.update(start=start, end=end)
    if edited != segment:
        edited["edited"] = True
        edited["words_current"] = True
    # A partial source stays partial: a single manual edit does not prove that
    # the model's remaining timing/coverage claims are complete.
    edited["word_timing"] = bool(segment.get("word_timing"))
    from .transcript_merge import WORD_FIELDS, _valid_timing

    # Existing saved words may carry unrelated provenance. Validate timing on
    # its known fields without dropping those extra fields from the saved result.
    timing_view = {
        **edited,
        "words": [
            {key: value for key, value in word.items() if key in WORD_FIELDS}
            for word in edited["words"]
        ],
    }
    valid, _ = _valid_timing(timing_view, segment)
    if not valid:
        raise ValueError("The saved word timing is inconsistent. Review the passage first.")
    return edited


def _passage_index(segments, identifier):
    matches = [index for index, passage in enumerate(segments) if passage["id"] == identifier]
    if not matches:
        raise KeyError(identifier)
    if len(matches) != 1:
        raise ValueError("The saved result has ambiguous passage identifiers.")
    return matches[0]


def delete_passage(segments, identifier):
    """Remove one selected passage; exact earlier content remains in result history."""
    index = _passage_index(segments, identifier)
    return segments[:index] + segments[index + 1 :]


def _complete_words(segment):
    """Check the saved mapping itself before using a complete-timing claim to split."""
    if not segment.get("word_timing") or not segment.get("words"):
        return False
    text, previous_char, previous_start, previous_end = segment["text"], 0, -1, -1
    uncovered = []
    try:
        start, end = _interval(segment)
        for word in segment["words"]:
            left, right = word["char_start"], word["char_end"]
            ws, we = _interval(word)
            if (
                type(left) is not int
                or type(right) is not int
                or not previous_char <= left < right <= len(text)
                or text[left:right] != word["word"]
                or ws is None
                or we is None
                or not previous_start <= ws < we
                or we < previous_end
                or (start is not None and ws < start)
                or (end is not None and we > end)
            ):
                return False
            uncovered.append(text[previous_char:left])
            previous_char, previous_start, previous_end = right, ws, we
        uncovered.append(text[previous_char:])
    except (KeyError, TypeError, ValueError):
        return False
    return not re.search(r"\w", "".join(uncovered))


def _shift_words(words, offset):
    return [
        {
            **copy.deepcopy(word),
            "char_start": word["char_start"] + offset,
            "char_end": word["char_end"] + offset,
        }
        for word in words
    ]


def _recompute_word_timing(segment):
    segment["word_timing"] = _complete_words({**segment, "word_timing": True})
    if segment["word_timing"] and segment["start"] is None and segment["end"] is None:
        # A fully located half has its own real word bounds, even when its
        # parent also contained an unlocated fragment. Never time that fragment.
        segment["start"] = segment["words"][0]["start"]
        segment["end"] = segment["words"][-1]["end"]


def merge_passages(segments, identifier, direction, speaker_id=None, speakers=None):
    """Join adjacent passages without assigning an untimed part another part's bounds."""
    index = _passage_index(segments, identifier)
    if direction not in {"up", "down"}:
        raise ValueError("Choose Merge up or Merge down.")
    adjacent = index + (-1 if direction == "up" else 1)
    if not 0 <= adjacent < len(segments):
        raise ValueError("There is no adjacent passage in that direction.")
    first, last = sorted((index, adjacent))
    left, right = segments[first], segments[last]
    known = {passage["speaker"] for passage in segments} | set(speakers or {})
    if speaker_id is None:
        if left["speaker"] != right["speaker"]:
            raise ValueError("Choose a speaker explicitly when merging different speakers.")
        speaker_id = left["speaker"]
    elif speaker_id not in known:
        raise ValueError("Choose an existing speaker from this result.")
    merged = copy.deepcopy(left)
    merged["text"] = left["text"] + " " + right["text"]
    if len(merged["text"]) > 200_000:
        raise ValueError("A merged passage must be at most 200,000 characters.")
    merged["original_text"] = left["original_text"] + " " + right["original_text"]
    if len(merged["original_text"]) > 200_000:
        raise ValueError("A merged original passage must be at most 200,000 characters.")
    merged["speaker"] = speaker_id
    bounds = [_interval(passage) for passage in (left, right)]
    if all(start is not None and end is not None and start < end for start, end in bounds):
        merged["start"] = min(start for start, _ in bounds)
        merged["end"] = max(end for _, end in bounds)
    else:
        merged["start"] = merged["end"] = None
    merged["words"] = []
    for passage, offset in ((left, 0), (right, len(left["text"]) + 1)):
        # Free rewrites retain old words for restoration, never for current playback.
        if word_mapping_current(passage):
            merged["words"].extend(_shift_words(passage.get("words", []), offset))
    merged["original_words"] = copy.deepcopy(left["original_words"]) + _shift_words(
        right["original_words"], len(left["original_text"]) + 1
    )
    # Current mappings can be partial even after a valid word correction. Keep
    # that validity separate from completeness and the general review flag.
    merged["words_current"] = True
    merged["word_timing"] = True
    merged["word_timing"] = _complete_words(merged)
    original = {
        **merged,
        "text": merged["original_text"],
        "words": merged["original_words"],
        "word_timing": True,
    }
    merged["original_word_timing"] = _complete_words(original)
    merged["edited"] = bool(left.get("edited") or right.get("edited"))
    return segments[:first] + [merged] + segments[last + 1 :]


def split_passage(segments, identifier, cursor, split_time=None):
    """Split exact code-point text using real words or a deliberate audio cursor."""
    index = _passage_index(segments, identifier)
    passage = segments[index]
    text = passage["text"]
    if (
        type(cursor) is not int
        or not 0 < cursor < len(text)
        or not text[:cursor].strip()
        or not text[cursor:].strip()
    ):
        raise ValueError("Place the text cursor between two nonempty passage parts.")
    left, right = copy.deepcopy(passage), copy.deepcopy(passage)
    left["text"], right["text"] = text[:cursor], text[cursor:]
    right["id"] = uuid.uuid4().hex
    while any(passage["id"] == right["id"] for passage in segments):
        right["id"] = uuid.uuid4().hex
    complete = _complete_words(passage)
    if passage.get("word_timing") and not complete and split_time is None:
        raise ValueError("The saved word mapping is incomplete. Use a passage audio split time.")
    if complete:
        if split_time is not None:
            raise ValueError(
                "Complete word timing determines this split; omit the audio split time."
            )
        words = passage["words"]
        if any(word["char_start"] < cursor < word["char_end"] for word in words):
            raise ValueError("Split between timed words, not inside a timed word.")
        count = sum(word["char_end"] <= cursor for word in words)
        if not 0 < count < len(words):
            raise ValueError("Each part must contain at least one timed word.")
        left["words"] = copy.deepcopy(words[:count])
        right["words"] = _shift_words(words[count:], -cursor)
        start, end = _interval(passage)
        left["start"] = start if start is not None else words[0]["start"]
        left["end"] = words[count - 1]["end"]
        right["start"] = words[count]["start"]
        right["end"] = end if end is not None else words[-1]["end"]
        # Word corrections preserve one-to-one interval identity. Restore each half
        # to its corresponding original words, not offsets from the corrected text.
        original = {
            **passage,
            "text": passage["original_text"],
            "words": passage["original_words"],
            "word_timing": passage["original_word_timing"],
        }
        aligned_original = _complete_words(original) and len(original["words"]) == len(words)
        aligned_original = aligned_original and all(
            (current["start"], current["end"]) == (source["start"], source["end"])
            for current, source in zip(words, original["words"], strict=True)
        )
        if aligned_original:
            boundary = original["words"][count]["char_start"] - (
                words[count]["char_start"] - cursor
            )
            if (
                not original["words"][count - 1]["char_end"]
                <= boundary
                <= original["words"][count]["char_start"]
            ):
                boundary = original["words"][count]["char_start"]
            left["original_text"] = original["text"][:boundary]
            right["original_text"] = original["text"][boundary:]
            left["original_words"] = copy.deepcopy(original["words"][:count])
            right["original_words"] = _shift_words(original["words"][count:], -boundary)
        else:
            for part in (left, right):
                part["original_text"] = part["text"]
                part["original_words"] = copy.deepcopy(part["words"])
                part["original_word_timing"] = True
    else:
        start, end = _interval(passage)
        if start is None and end is None:
            if split_time is not None:
                raise ValueError("An untimed passage cannot use an audio split time.")
        elif start is None or end is None or start >= end:
            raise ValueError("Splitting this passage needs complete source bounds.")
        else:
            split_time = timestamp(split_time)
            if split_time is None or not start < split_time < end:
                raise ValueError("Choose an audio split time strictly within this passage.")
            left["end"], right["start"] = split_time, split_time
        # Earlier source/original mappings remain exactly recoverable in history.
        # New halves restore to their own baseline, never a stale full-passage map.
        for part, begin, finish in ((left, 0, cursor), (right, cursor, len(text))):
            part.update(words=[], word_timing=False, original_word_timing=False)
            if word_mapping_current(passage):
                for word in passage.get("words", []):
                    ws, we = _interval(word)
                    a, b = word["char_start"], word["char_end"]
                    if (
                        type(a) is not int
                        or type(b) is not int
                        or not begin <= a < b <= finish
                        or text[a:b] != word["word"]
                        or (part["start"] is not None and ws is not None and ws < part["start"])
                        or (part["end"] is not None and we is not None and we > part["end"])
                    ):
                        continue
                    part["words"].extend(_shift_words([word], -begin))
            _recompute_word_timing(part)
            part["original_word_timing"] = part["word_timing"]
            part["original_text"] = part["text"]
            part["original_words"] = copy.deepcopy(part["words"])
    for part in (left, right):
        part["words_current"] = True
        part["edited"] = (
            part["text"] != part["original_text"] or part["words"] != part["original_words"]
        )
    return segments[:index] + [left, right] + segments[index + 1 :]


def split_passage_manual(segments, identifier, cursor, split_time=None):
    """Split between lexical words even when their audio location is unknown."""
    index = _passage_index(segments, identifier)
    passage, text = segments[index], segments[index].get("text")
    if (
        not isinstance(text, str)
        or len(text) > 200_000
        or type(cursor) is not int
        or not 0 < cursor < len(text)
        or not text[:cursor].strip()
        or not text[cursor:].strip()
    ):
        raise ValueError("Place the text cursor between two nonempty passage parts.")

    if not (text[cursor - 1].isspace() or text[cursor].isspace()):
        raise ValueError("Place the text cursor between words, not inside a word.")
    tokens = list(re.finditer(r"\S+", text))
    groups, uncertain = _manual_word_groups(passage, tokens)
    if not uncertain and _complete_words(passage):
        try:
            return split_passage(segments, identifier, cursor, split_time)
        except (KeyError, TypeError, ValueError, IndexError):
            # Valid lexical edits must not depend on an optional timing/baseline map.
            pass
    source_words = [word for group in groups for word in group]
    bounds, audio_split = _manual_interval(passage), None
    if bounds is not None and all(value is not None for value in bounds):
        candidate = _manual_interval({"start": split_time})
        if (
            candidate is not None
            and candidate[0] is not None
            and bounds[0] < candidate[0] < bounds[1]
        ):
            audio_split = candidate[0]
    parts = []
    for begin, finish in ((0, cursor), (cursor, len(text))):
        part = copy.deepcopy(passage)
        part.update(text=text[begin:finish], start=None, end=None, words=[])
        if audio_split is not None:
            part["start"], part["end"] = (
                (bounds[0], audio_split) if begin == 0 else (audio_split, bounds[1])
            )
        retained = []
        for word in source_words:
            if not begin <= word["char_start"] < word["char_end"] <= finish:
                continue
            copied = _shift_words([word], -begin)[0]
            if audio_split is not None and any(
                value is not None and not part["start"] <= value <= part["end"]
                for value in (copied.get("start"), copied.get("end"))
            ):
                copied = _manual_untimed(part["text"], copied["char_start"], copied["char_end"])
            retained.append(copied)
        part_tokens = list(re.finditer(r"\S+", part["text"]))
        starts, part_groups = [token.start() for token in part_tokens], [[] for _ in part_tokens]
        for word in retained:
            owner = bisect_right(starts, word["char_start"]) - 1
            if owner >= 0 and word["char_end"] <= part_tokens[owner].end():
                part_groups[owner].append(word)
        for token, group in zip(part_tokens, part_groups, strict=True):
            part["words"].extend(_manual_token_words(part["text"], token, group))
        _recompute_word_timing(part)
        # Each half restores to this local baseline. The exact earlier originals
        # and source bounds remain in its immutable predecessor, not guessed here.
        part.update(
            original_text=part["text"],
            original_words=copy.deepcopy(part["words"]),
            original_word_timing=part["word_timing"],
            words_current=True,
            edited=False,
        )
        parts.append(part)
    parts[1]["id"] = uuid.uuid4().hex
    while any(passage["id"] == parts[1]["id"] for passage in segments):
        parts[1]["id"] = uuid.uuid4().hex
    return segments[:index] + parts + segments[index + 1 :]


def format_time(seconds):
    if seconds is None:
        return "Untimed"
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def _export_ticks(seconds, units):
    scaled = timestamp(seconds) * units
    if not math.isfinite(scaled):
        raise ValueError(
            "The transcript timestamp is outside this export's supported range. "
            "Use Readable text, CSV or JSON."
        )
    return math.floor(scaled + 0.5)


def _nvivo_time(seconds, mac=False):
    ticks = _export_ticks(seconds, 10)
    seconds, tenth = divmod(ticks, 10)
    clock = f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    return f"#{clock}-{tenth}#" if mac else f"{clock}.{tenth}"


def export_nvivo(segments, speakers=None, grouping="speaker", export_format="text"):
    """Readable legacy text or a documented Windows/Mac media-transcript format."""
    speakers = resolved_speakers(segments, speakers or {})
    if export_format not in {"text", "nvivo-windows", "nvivo-mac"}:
        raise ValueError("Choose readable text, NVivo Windows, or NVivo Mac.")
    speakers = speakers or {}
    if export_format != "text":
        if not segments:
            raise ValueError("There is no transcript to export.")
        rows = [["Timespan", "Speaker", "Content"]]
        paragraphs = []
        for segment in segments:
            start, end = _interval(segment)
            if start is None:
                raise ValueError(
                    "NVivo transcript export requires a start time for every passage. "
                    "Use Readable text to keep untimed passages."
                )
            # One physical line per cell; saved text and original timings stay untouched.
            text = " ".join(segment["text"].split())
            speaker = " ".join(speakers.get(segment["speaker"], segment["speaker"]).split())
            if export_format == "nvivo-mac":
                ending = f" {_nvivo_time(end, mac=True)}" if end is not None else ""
                paragraphs.append(f"{_nvivo_time(start, mac=True)}\n{speaker}: {text}{ending}")
            else:
                timespan = _nvivo_time(start)
                if end is not None:
                    timespan += "-" + _nvivo_time(end)
                rows.append([timespan, speaker, text])
        if export_format == "nvivo-mac":
            return "\n\n".join(paragraphs) + "\n"
        return "\r\n".join("\t".join(row) for row in rows) + "\r\n"
    if grouping not in {"speaker", "timestamp"}:
        raise ValueError("Choose speaker or timestamp grouping.")
    lines, previous = ["INTERVIEW"], object()
    for segment in segments:
        label = (
            speakers.get(segment["speaker"], segment["speaker"])
            if grouping == "speaker"
            else format_time(segment["start"])
        )
        if label != previous:
            lines.extend(["", label])
            previous = label
        lines.append(segment["text"].strip())
    return "\n".join(lines) + "\n"


def export_filename(filename, today=None, export_format="text"):
    stem = Path(filename.replace("\\", "/")).stem
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")[:120] or "transcript"
    if export_format in {"srt", "vtt", "csv", "json", "docx"}:
        suffix = "." + export_format
    else:
        suffix = ("" if export_format == "text" else "." + export_format) + ".txt"
    prefix = f"{today or date.today()} - "
    # Bound the complete component in UTF-8 bytes, also covering Windows UTF-16 units.
    budget = 255 - len((prefix + suffix).encode("utf-8"))
    stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    return prefix + stem + suffix


def export_transcript(segments, speakers=None, grouping="speaker", export_format="text"):
    """Export the current view; never disclose discarded original words or setup settings."""
    speakers = resolved_speakers(segments, speakers or {})
    if export_format in {"text", "nvivo-windows", "nvivo-mac"}:
        return export_nvivo(segments, speakers, grouping, export_format)
    if not segments:
        raise ValueError("There is no transcript to export.")
    if export_format == "json":
        return (
            json.dumps(
                {
                    "format": "cbs-transcriber",
                    "version": 1,
                    "speakers": speakers,
                    "segments": [
                        {key: s[key] for key in ("start", "end", "speaker", "text", "word_timing")}
                        | {"words": s["words"] if s["word_timing"] else []}
                        for s in segments
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    if export_format == "csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["start", "end", "speaker", "text"])
        for segment in segments:
            # Spreadsheet quoting alone does not neutralize formulas.
            cells = [
                speakers.get(segment["speaker"], segment["speaker"]),
                segment["text"],
            ]
            cells = ["'" + v if v.lstrip().startswith(("=", "+", "-", "@")) else v for v in cells]
            writer.writerow([segment["start"], segment["end"], *cells])
        return output.getvalue()
    if export_format not in {"srt", "vtt"}:
        raise ValueError("Choose a supported transcript export format.")
    blocks = []
    previous = -1
    for index, segment in enumerate(segments, 1):
        start, end = _interval(segment)
        if start is None or end is None:
            raise ValueError(
                "Subtitle export needs start and end times for every passage. "
                "Use Readable text, CSV or JSON for untimed passages."
            )
        left, right = (_export_ticks(t, 1000) for t in (start, end))
        if right <= left or left < previous:
            raise ValueError("Subtitles need ordered, positive millisecond passage intervals.")
        previous = left

        def clock(ticks):
            seconds, milliseconds = divmod(ticks, 1000)
            separator = "," if export_format == "srt" else "."
            return (
                f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
                f"{separator}{milliseconds:03d}"
            )

        text = html.escape(" ".join(segment["text"].split()), quote=False)
        speaker = html.escape(
            " ".join(speakers.get(segment["speaker"], segment["speaker"]).split()), quote=False
        )
        if segment["speaker"] != "UNKNOWN":
            text = f"{speaker}: {text}" if export_format == "srt" else f"<v {speaker}>{text}</v>"
        blocks.append(f"{index}\n{clock(left)} --> {clock(right)}\n{text}")
    return ("WEBVTT\n\n" if export_format == "vtt" else "") + "\n\n".join(blocks) + "\n\n"


def combine_chunks(chunks):
    rows = []
    for index, chunk in enumerate(chunks):
        for segment in chunk["segments"]:
            row = copy.deepcopy(segment)
            for field in ("start", "end"):
                if row[field] is not None:
                    row[field] += chunk["offset"]
            for field in ("words", "original_words"):
                for word in row[field]:
                    for time_field in ("start", "end"):
                        if word[time_field] is not None:
                            word[time_field] += chunk["offset"]
            if len(chunks) > 1 and row["speaker"] != "UNKNOWN":
                # Independent API calls cannot establish the same speaker across chunks.
                row["speaker"] = f"Part {index + 1} · {row['speaker']}"
            rows.append(row)
    for index, row in enumerate(rows):
        row["id"] = str(index)
    return rows
