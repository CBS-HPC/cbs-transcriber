"""History change list: bounded word-level comparison of exact saved versions (ADR 0181).

Read-only. Both versions are read and integrity-checked in one database snapshot; the
comparison runs after the snapshot closes. Passages are matched by identity: each is
unchanged, changed (text, speaker and/or word timing), added or removed. Text differences
are whitespace-token diffs from the standard library, with long equal runs collapsed.
"""

import difflib
import json
import math

from .history import decode_payload
from .transcripts import default_speaker_names, saved_speaker_names

MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_PASSAGES = 10_000
MAX_LISTED = 200
MAX_WORDS = 2_000
CONTEXT_WORDS = 3
MAX_CONTEXT_BYTES = 64 * 1024
# Bounded, allowlisted change metadata the History list may show (never text or receipts).
LIST_CONTEXT = (
    "segment_id",
    "adjacent_segment_id",
    "direction",
    "restored_from_version_id",
    "deleted_version_id",
    "undone_version_id",
    "redone_version_id",
    "undo_version_id",
    "requested_action",
)
SUMMARY_SOURCES = {"result_merge": "merge", "result_conflict_review": "conflict_review"}


def list_context(action, context):
    """The History list's bounded change metadata for one version row."""
    if not isinstance(context, dict):
        return {}
    shown = {
        key: context[key]
        for key in LIST_CONTEXT
        if isinstance(context.get(key), str) and 0 < len(context[key]) <= 200
    }
    evidence = context.get(SUMMARY_SOURCES.get(action, ""))
    summary = evidence.get("summary") if isinstance(evidence, dict) else None
    if isinstance(summary, dict) and all(
        isinstance(k, str) and type(v) is int and 0 <= v <= 100_000 for k, v in summary.items()
    ):
        shown["summary"] = dict(sorted(summary.items())[:12])
    return shown


class TooLarge(ValueError):
    """A version exceeds the comparison bounds; download both versions instead."""


def _row(db, recording_id, version_id):
    row = db.execute(
        "SELECT v.id,v.result_id,v.sequence,v.action,v.predecessor_id,v.payload_sha256,"
        "length(CAST(v.payload AS BLOB)) AS size,"
        "CASE WHEN length(CAST(v.payload AS BLOB))<=? THEN v.payload END AS payload "
        "FROM result_versions v JOIN transcript_results r ON r.id=v.result_id "
        "WHERE v.id=? AND r.recording_id=?",
        (MAX_PAYLOAD_BYTES, version_id, recording_id),
    ).fetchone()
    if row is None:
        raise KeyError(version_id)
    return dict(row)


def _decode(row):
    if row["payload"] is None:
        raise TooLarge("This version is too large to compare here; download both versions.")
    payload = decode_payload(row["payload"], row["payload_sha256"])
    if len(payload["segments"]) > MAX_PASSAGES:
        raise TooLarge("This version is too large to compare here; download both versions.")
    return payload


def _tokens(before, after):
    """Diff operations over whitespace tokens; long equal runs keep a little context."""
    left, right = before.split(), after.split()
    if len(left) > MAX_WORDS or len(right) > MAX_WORDS:
        return None
    output = []
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            run = left[i1:i2]
            if len(run) > 2 * CONTEXT_WORDS + 1:
                output.append({"op": "equal", "text": " ".join(run[:CONTEXT_WORDS])})
                output.append({"op": "skip", "count": len(run) - 2 * CONTEXT_WORDS})
                output.append({"op": "equal", "text": " ".join(run[-CONTEXT_WORDS:])})
            else:
                output.append({"op": "equal", "text": " ".join(run)})
            continue
        if tag in ("delete", "replace"):
            output.append({"op": "delete", "text": " ".join(left[i1:i2])})
        if tag in ("insert", "replace"):
            output.append({"op": "insert", "text": " ".join(right[j1:j2])})
    return output


def _whole(op, text):
    words = text.split()
    if len(words) > MAX_WORDS:
        return None
    return [{"op": op, "text": " ".join(words)}] if words else []


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _timing(passage):
    words = passage.get("words") if isinstance(passage.get("words"), list) else []
    return (
        _number(passage.get("start")),
        _number(passage.get("end")),
        [(_number(w.get("start")), _number(w.get("end"))) for w in words if isinstance(w, dict)],
    )


def _names(payload):
    defaults = default_speaker_names(payload["segments"], payload["speakers"])
    saved = saved_speaker_names(payload["speakers"])
    return lambda label: saved.get(label) or defaults.get(label) or str(label)


def _passage(kind, passage, position, name, *, other=None, other_name=None):
    item = {
        "segment_id": passage.get("id"),
        "position": position + 1,
        "kind": kind,
        "speaker": name(passage.get("speaker")),
    }
    text = passage.get("text") if isinstance(passage.get("text"), str) else ""
    if kind == "added":
        item["tokens"] = _whole("insert", text)
    elif kind == "removed":
        item["tokens"] = _whole("delete", text)
    else:
        before = other.get("text") if isinstance(other.get("text"), str) else ""
        changes = []
        if before != text:
            changes.append("text")
            item["tokens"] = _tokens(before, text)
        # A rename changes the speaker list once; a passage changes only with its raw label.
        if other.get("speaker") != passage.get("speaker"):
            changes.append("speaker")
            item["speaker_before"] = other_name(other.get("speaker"))
        old, new = _timing(other), _timing(passage)
        if old != new:
            changes.append("timing")
            item["timing_words"] = sum(
                1 for a, b in zip(old[2], new[2], strict=False) if a != b
            ) + abs(len(old[2]) - len(new[2]))
        item["changes"] = changes
    if "tokens" in item and item["tokens"] is None:
        item["rewritten"] = True
        item.pop("tokens")
    return item


def compare(before, after):
    """Changes from ``before`` to ``after`` (decoded payloads); ``before`` may be None."""
    old_name, new_name = (_names(before) if before else None), _names(after)
    old = {p.get("id"): p for p in before["segments"]} if before else {}
    new_ids = {p.get("id") for p in after["segments"]}
    items, counts = [], {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
    if before:
        # Removed passages are listed where they stood, before the next surviving passage.
        for position, passage in enumerate(before["segments"]):
            if passage.get("id") not in new_ids:
                counts["removed"] += 1
                items.append((position - 0.5, _passage("removed", passage, position, old_name)))
    for position, passage in enumerate(after["segments"]):
        previous = old.get(passage.get("id"))
        if previous is None:
            counts["added"] += 1
            items.append((position, _passage("added", passage, position, new_name)))
            continue
        if json.dumps(previous, sort_keys=True, ensure_ascii=False) == json.dumps(
            passage, sort_keys=True, ensure_ascii=False
        ):
            counts["unchanged"] += 1
            continue
        item = _passage("changed", passage, position, new_name, other=previous, other_name=old_name)
        if not item["changes"]:
            item["changes"] = ["details"]
        counts["changed"] += 1
        items.append((position, item))
    items.sort(key=lambda pair: pair[0])
    labels = []
    if before:
        for label in sorted(
            set(before["speakers"]) | set(after["speakers"]),
            key=str,
        ):
            if before["speakers"].get(label) != after["speakers"].get(label):
                labels.append(
                    {
                        "label": str(label),
                        "before": old_name(label),
                        "after": new_name(label),
                    }
                )
    listed = [item for _, item in items]
    return {
        "counts": counts,
        "passages": listed[:MAX_LISTED],
        "truncated": len(listed) > MAX_LISTED,
        "speaker_names": labels[:MAX_LISTED],
    }


def changes(store, recording_id, result_id, version_id, against=None):
    """What changed in ``version_id`` relative to its predecessor or an explicit version."""
    with store.connection() as db:
        db.execute("BEGIN")
        row = _row(db, recording_id, version_id)
        if row["result_id"] != result_id:
            raise KeyError(version_id)
        base_id = against if against is not None else row["predecessor_id"]
        base = _row(db, recording_id, base_id) if base_id else None
    description = {
        "version_id": row["id"],
        "sequence": row["sequence"],
        "action": row["action"],
        "base": None
        if base is None
        else {
            "version_id": base["id"],
            "result_id": base["result_id"],
            "sequence": base["sequence"],
            "action": base["action"],
        },
    }
    try:
        after = _decode(row)
        before = _decode(base) if base is not None else None
    except TooLarge as error:
        return {**description, "too_large": True, "message": str(error)}
    return {**description, "too_large": False, **compare(before, after)}
