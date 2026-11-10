"""Conservative field merge of a derived transcript; no persistence or identity lookup."""

import copy
import math

from .history import encode_payload
from .transcripts import (
    MAX_TRANSCRIPT_BYTES,
    _complete_words,
    saved_speaker_names,
    word_mapping_current,
)

MAX_MERGE_BYTES = MAX_TRANSCRIPT_BYTES
MAX_PASSAGES = 100_000
MAX_DETAILS = 200
POLICY_VERSION = "cbs-transcript-merge-v1"
TIMING_FIELDS = ("start", "end", "words", "word_timing", "words_current")
WORD_FIELDS = {
    "word",
    "start",
    "end",
    "char_start",
    "char_end",
    "manual_timing",
    "alignment_hints_stale",
}


def _identifier(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 200
        and not any(ord(character) < 32 for character in value)
    )


def _index(payload):
    if not isinstance(payload, dict):
        raise ValueError("A merge snapshot must contain passages and speaker names.")
    segments, speakers = payload.get("segments"), payload.get("speakers")
    if (
        not isinstance(segments, list)
        or len(segments) > MAX_PASSAGES
        or not isinstance(speakers, dict)
        or len(speakers) > MAX_PASSAGES
        or any(
            not _identifier(raw) or not isinstance(name, str) or len(name) > 2000
            for raw, name in speakers.items()
        )
    ):
        raise ValueError("The merge snapshot exceeds its passage or speaker limits.")
    indexed = {}
    for passage in segments:
        if (
            not isinstance(passage, dict)
            or not _identifier(passage.get("id"))
            or not isinstance(passage.get("text"), str)
            or len(passage["text"]) > 200_000
            or not _identifier(passage.get("speaker", "UNKNOWN"))
            or passage["id"] in indexed
        ):
            raise ValueError("Merge passages need unique bounded IDs and literal text.")
        indexed[passage["id"]] = passage
    return indexed


def _timing(passage):
    return {
        "start": passage.get("start"),
        "end": passage.get("end"),
        "words": passage.get("words", []),
        "word_timing": passage.get("word_timing", False),
        # An absent marker can still describe a valid unedited/complete mapping.
        "words_current": word_mapping_current(passage),
    }


def _number(value):
    try:
        return type(value) in {int, float} and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _valid_timing(passage, base):
    """Validate real mappings without promoting partial coverage or synthesizing bounds."""
    start, end, words = passage.get("start"), passage.get("end"), passage.get("words", [])
    if (
        any(value is not None and not _number(value) for value in (start, end))
        or (start is not None and end is not None and start >= end)
        or type(passage.get("word_timing", False)) is not bool
        or ("words_current" in passage and type(passage["words_current"]) is not bool)
        or not isinstance(words, list)
        or not word_mapping_current(passage)
        or any(base.get(key) is not None and passage.get(key) is None for key in ("start", "end"))
    ):
        return False, 0
    previous_char, previous_start, previous_end, timed = 0, -1, -1, 0
    for word in words:
        if (
            not isinstance(word, dict)
            or word.keys() - WORD_FIELDS
            or any(
                key in word and word[key] is not True
                for key in ("manual_timing", "alignment_hints_stale")
            )
        ):
            return False, 0
        left, right = word.get("char_start"), word.get("char_end")
        ws, we = word.get("start"), word.get("end")
        if (
            type(left) is not int
            or type(right) is not int
            or not previous_char <= left < right <= len(passage["text"])
            or passage["text"][left:right] != word.get("word")
        ):
            return False, 0
        previous_char = right
        if ws is None and we is None:
            continue
        if (
            not _number(ws)
            or not _number(we)
            or not previous_start <= ws < we
            or we < previous_end
            or (start is not None and ws < start)
            or (end is not None and we > end)
        ):
            return False, 0
        previous_start, previous_end, timed = ws, we, timed + 1
    if passage.get("word_timing", False) and not _complete_words(passage):
        return False, 0
    if not timed:
        # Never replace an existing mapping with a failed/empty alignment.
        return bool(
            not words and not base.get("words") and start is not None and end is not None
        ), 0
    return True, timed


def merge_payload(base, ours, incoming, creation):
    """Return safe changes and bounded explanations; callers verify frozen lineage/CAS.

    Conflicts count passages with any conflicting group, including a passage whose other
    group can update. Skipped and unchanged count passages with no applied/conflicting group.
    Deleted base passages are reported without resurrecting their text. Incoming-only IDs
    never introduce passages; the changed/missing mapped source passages remain visible.
    """
    indexes, size = [], 0
    for payload in (base, ours, incoming, creation):
        indexes.append(_index(payload))
        try:
            encoded, _ = encode_payload(payload["segments"], payload["speakers"])
            size += len(encoded.encode("utf-8"))
        except (TypeError, ValueError, RecursionError, OverflowError):
            raise ValueError("Merge snapshots must contain finite, bounded JSON values.") from None
        if size > MAX_MERGE_BYTES:
            raise ValueError("The four merge snapshots exceed the bounded payload limit.")
    base_by_id, ours_by_id, incoming_by_id, _ = indexes
    if len(base["segments"]) != len(creation["segments"]) or any(
        left["text"] != right["text"]
        for left, right in zip(base["segments"], creation["segments"], strict=True)
    ):
        raise ValueError(
            "The derived result changed passage boundaries or wording when it was created. "
            "Compare it manually; its passage correspondence is not proven."
        )
    incoming_id = {
        left["id"]: right["id"]
        for left, right in zip(base["segments"], creation["segments"], strict=True)
    }
    result = copy.deepcopy(ours["segments"])
    speakers = copy.deepcopy(ours["speakers"])
    named = saved_speaker_names(ours["speakers"])
    base_labels = {passage.get("speaker", "UNKNOWN") for passage in base["segments"]}
    ours_labels = {passage.get("speaker", "UNKNOWN") for passage in ours["segments"]}
    summary = dict.fromkeys(
        (
            "updated_passages",
            "timing_updates",
            "speaker_updates",
            "conflicts",
            "skipped",
            "unchanged",
        ),
        0,
    )
    passages = []

    def report(identifier, text, changes, reasons, conflict=False):
        if changes:
            status = "updated"
            summary["updated_passages"] += 1
            summary["timing_updates"] += "timing" in changes
            summary["speaker_updates"] += "speaker" in changes
        else:
            status = "conflict" if conflict else "skipped" if reasons else "unchanged"
            if not conflict:
                summary[status] += 1
        summary["conflicts"] += conflict
        if len(passages) < MAX_DETAILS:
            passages.append(
                {
                    "id": identifier,
                    "status": status,
                    "text": text,
                    "changes": changes,
                    "reasons": reasons,
                }
            )

    for current, updated in zip(ours["segments"], result, strict=True):
        identifier = current["id"]
        original = base_by_id.get(identifier)
        if original is None:
            report(
                identifier, current["text"], [], ["This reviewed passage has no common-base match."]
            )
            continue
        theirs = incoming_by_id.get(incoming_id[identifier])
        if theirs is None:
            report(
                identifier,
                current["text"],
                [],
                ["The incoming passage was removed or restructured."],
            )
            continue
        if current["text"] != original["text"]:
            conflict = theirs["text"] not in {original["text"], current["text"]}
            report(
                identifier,
                current["text"],
                [],
                ["Both versions changed the wording; reviewed text was kept."]
                if conflict
                else ["Reviewed wording or structure changed; the passage was kept."],
                conflict,
            )
            continue
        if theirs["text"] != original["text"]:
            report(
                identifier,
                current["text"],
                [],
                ["Incoming wording changed and was not adopted."],
                True,
            )
            continue
        changes, reasons, conflict = [], [], False
        before, current_timing, proposed = _timing(original), _timing(current), _timing(theirs)
        if proposed != before and current_timing != proposed:
            if current_timing != before:
                reasons.append("Both versions changed timing; reviewed timing was kept.")
                conflict = True
            else:
                valid, timed = _valid_timing(theirs, original)
                if not valid:
                    reasons.append(
                        "Incoming timing has no valid current word mapping or passage bounds."
                    )
                    conflict = True
                else:
                    updated.update(
                        start=theirs.get("start"),
                        end=theirs.get("end"),
                        words=copy.deepcopy(theirs.get("words", [])),
                        word_timing=theirs.get("word_timing", False),
                    )
                    if timed:
                        updated["words_current"] = True
                    else:
                        updated.pop("words_current", None)
                    changes.append("timing")
        before_label = original.get("speaker", "UNKNOWN")
        current_label = current.get("speaker", "UNKNOWN")
        proposed_label = theirs.get("speaker", "UNKNOWN")
        if proposed_label != before_label and current_label != proposed_label:
            if current_label != before_label:
                reasons.append(
                    "Both versions changed the speaker; the reviewed assignment was kept."
                )
                conflict = True
            elif current_label in named:
                reasons.append("The named reviewed speaker was kept.")
            elif proposed_label == "UNKNOWN":
                reasons.append("The incoming speaker is unknown; the reviewed assignment was kept.")
            elif (
                proposed_label not in base_labels
                and (proposed_label in ours_labels or proposed_label in ours["speakers"])
            ) or (
                proposed_label in speakers
                and proposed_label in incoming["speakers"]
                and speakers[proposed_label] != incoming["speakers"][proposed_label]
            ):
                reasons.append(
                    "The incoming speaker label conflicts with a reviewed voice or name."
                )
                conflict = True
            else:
                updated["speaker"] = proposed_label
                if proposed_label not in speakers and proposed_label in incoming["speakers"]:
                    speakers[proposed_label] = incoming["speakers"][proposed_label]
                changes.append("speaker")
        report(identifier, current["text"], changes, reasons, conflict)
    for identifier in base_by_id:
        if identifier not in ours_by_id:
            report(identifier, "", [], ["The reviewed passage was deleted; it was not restored."])
    return {"segments": result, "speakers": speakers, "summary": summary, "passages": passages}
