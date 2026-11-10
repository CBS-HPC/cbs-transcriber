"""Bounded, explicit enhancement conflict decisions and exact historical reconstruction."""

import copy
import hashlib
import hmac
import json

from . import result_merges
from .history import conflict_review_metadata, encode_payload, validate_reason
from .result_actions import append_edit
from .transcript_merge import MAX_DETAILS, _index, _timing, _valid_timing, merge_payload
from .transcripts import MAX_TRANSCRIPT_BYTES, edit_segment

FORMAT = "cbs-result-conflict-review-v1"
GROUPS = ("wording", "timing", "speaker")
SUMMARY_FIELDS = (
    "decisions",
    "incoming",
    "current",
    "changed_passages",
    "wording_updates",
    "timing_updates",
    "speaker_updates",
)


def validate_decisions(value):
    if not isinstance(value, list) or len(value) > MAX_DETAILS * len(GROUPS):
        raise ValueError("Choose at most 600 visible passage groups for review.")
    output, seen = [], set()
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {"passage_id", "group", "choice"}
            or not isinstance(item["passage_id"], str)
            or not 0 < len(item["passage_id"]) <= 200
            or any(ord(char) < 32 for char in item["passage_id"])
            or not isinstance(item["group"], str)
            or item["group"] not in GROUPS
            or not isinstance(item["choice"], str)
            or item["choice"] not in {"current", "incoming"}
        ):
            raise ValueError("Choose a visible passage group and Current or Incoming.")
        key = (item["passage_id"], item["group"])
        if key in seen:
            raise ValueError("Choose each passage group only once.")
        seen.add(key)
        output.append(dict(item))
    return sorted(output, key=lambda item: (item["passage_id"], item["group"]))


def _same_structure(passage, original):
    if passage is None or original is None:
        return False
    # Splits/joins change original text/words, while a literal free edit preserves them.
    if any(
        passage.get(field) != original.get(field) for field in ("original_text", "original_words")
    ):
        return False
    return passage["text"] == original["text"] or (
        isinstance(original.get("original_text"), str)
        and isinstance(original.get("original_words"), list)
    )


def _values(passage):
    if passage is None:
        return dict.fromkeys(GROUPS)
    return {
        "wording": passage["text"],
        "timing": _timing(passage),
        "speaker": passage.get("speaker", "UNKNOWN"),
    }


def _speaker_refusal(theirs, current, base, ours, incoming):
    proposed = theirs.get("speaker", "UNKNOWN")
    existing = current.get("speaker", "UNKNOWN")
    if proposed == "UNKNOWN" and existing != "UNKNOWN":
        return "An unknown incoming label cannot replace a known voice."
    base_labels = {item.get("speaker", "UNKNOWN") for item in base["segments"]}
    current_labels = {item.get("speaker", "UNKNOWN") for item in ours["segments"]}
    if proposed not in base_labels and proposed in current_labels and proposed != existing:
        return "The incoming label collides with a different reviewed voice."
    if (
        proposed in ours["speakers"]
        and proposed in incoming["speakers"]
        and ours["speakers"][proposed] != incoming["speakers"][proposed]
    ):
        return "The incoming label has a different saved name; reviewed names are retained."
    return None


def review_payload(base, current, incoming, creation, decisions):
    """Recompute every selected field from exact snapshots, preserving unselected dictionaries."""
    decisions = validate_decisions(decisions)
    # Reuse the established bounded lineage and creation-to-base correspondence checks.
    merge_payload(base, current, incoming, creation)
    indexes = [_index(item) for item in (base, current, incoming, creation)]
    base_index, current_index, incoming_index, creation_index = indexes
    mapping = {
        left["id"]: right["id"]
        for left, right in zip(base["segments"], creation["segments"], strict=True)
    }
    details, truncated = [], False
    ordinals = {passage["id"]: index + 1 for index, passage in enumerate(current["segments"])}
    for original in base["segments"]:
        identifier = original["id"]
        ours = current_index.get(identifier)
        created = creation_index[mapping[identifier]]
        theirs = incoming_index.get(mapping[identifier])
        values = {
            name: _values(passage)
            for name, passage in (("base", original), ("current", ours), ("incoming", theirs))
        }
        correspondence = _same_structure(ours, original) and _same_structure(theirs, created)
        if correspondence and values["base"] == values["current"] == values["incoming"]:
            continue
        if len(details) == MAX_DETAILS:
            truncated = True
            continue
        groups = {}
        for group in GROUPS:
            refusal = (
                None
                if correspondence
                else "Passage structure changed or a passage is missing; "
                "correspondence is unresolved."
            )
            if refusal is None and group == "wording" and not theirs["text"].strip():
                refusal = "Incoming wording must be nonblank."
            if refusal is None and group == "timing":
                if ours["text"] != theirs["text"]:
                    refusal = "Incoming timing requires exactly the current literal wording."
                elif not _valid_timing(theirs, ours)[0]:
                    refusal = "Incoming timing has no valid current word mapping or passage bounds."
            if refusal is None and group == "speaker":
                refusal = _speaker_refusal(theirs, ours, base, current, incoming)
            groups[group] = {
                **{name: value[group] for name, value in values.items()},
                "incoming_allowed": refusal is None,
                "refusal": refusal,
            }
        details.append({"id": identifier, "ordinal": ordinals.get(identifier), "groups": groups})
    # Current-only structural passages remain visible and cannot acquire guessed incoming data.
    for index, passage in enumerate(current["segments"]):
        if passage["id"] not in base_index:
            if len(details) == MAX_DETAILS:
                truncated = True
                continue
            details.append(
                {
                    "id": passage["id"],
                    "ordinal": index + 1,
                    "groups": {
                        group: {
                            "base": None,
                            "current": _values(passage)[group],
                            "incoming": None,
                            "incoming_allowed": False,
                            "refusal": "This reviewed passage has no proven common-base match.",
                        }
                        for group in GROUPS
                    },
                }
            )
    visible = {item["id"]: item for item in details[:MAX_DETAILS]}
    result = copy.deepcopy(current["segments"])
    positions = {passage["id"]: index for index, passage in enumerate(result)}
    speakers = copy.deepcopy(current["speakers"])
    summary = dict.fromkeys(SUMMARY_FIELDS, 0)
    changed = set()
    for decision in decisions:
        identifier, group, choice = (decision[key] for key in ("passage_id", "group", "choice"))
        if identifier not in visible:
            raise ValueError("Review choices must refer to the displayed bounded preview.")
        summary["decisions"] += 1
        summary[choice] += 1
        if choice == "current":
            continue
        detail = visible[identifier]["groups"][group]
        if not detail["incoming_allowed"]:
            raise ValueError(detail["refusal"])
        index = positions[identifier]
        passage = result[index]
        theirs = incoming_index[mapping[identifier]]
        before = copy.deepcopy(passage)
        if group == "wording":
            if passage["text"] != theirs["text"]:
                result[index] = edit_segment(passage, "free", theirs["text"])
        elif group == "timing":
            for field in ("start", "end", "words", "word_timing", "words_current"):
                if field in theirs:
                    passage[field] = copy.deepcopy(theirs[field])
                else:
                    passage.pop(field, None)
            # A validated legacy complete map need not already have an explicit marker.
            if theirs.get("words"):
                passage["words_current"] = True
        else:
            label = theirs.get("speaker", "UNKNOWN")
            passage["speaker"] = label
            if (
                label != before.get("speaker", "UNKNOWN")
                and label not in speakers
                and label in incoming["speakers"]
            ):
                speakers[label] = incoming["speakers"][label]
        if result[index] != before:
            changed.add(identifier)
            summary[group + "_updates"] += 1
    summary["changed_passages"] = len(changed)
    encoded, digest = encode_payload(result, speakers)
    if len(encoded.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
        raise ValueError("The reviewed result exceeds the supported snapshot size.")
    return {
        "segments": result,
        "speakers": speakers,
        "summary": summary,
        "passages": details,
        "truncated": truncated,
        "payload_sha256": digest,
    }


def _plan(store, db, recording_id, result_id, incoming_id, revision, decisions):
    lineage = result_merges._plan(store, db, recording_id, result_id, incoming_id, revision)
    snapshots = lineage["snapshots"]
    reviewed = review_payload(
        snapshots["base"],
        snapshots["current"],
        snapshots["incoming"],
        snapshots["creation"],
        decisions,
    )
    evidence = conflict_review_metadata(
        {
            "format": FORMAT,
            **lineage["references"],
            "decisions": validate_decisions(decisions),
            "summary": reviewed["summary"],
        }
    )
    binding = {
        **evidence,
        "recording_id": recording_id,
        "revision": revision,
        "current": result_merges._reference(lineage["ours_head"]),
        "proposed_payload_sha256": reviewed["payload_sha256"],
    }
    fingerprint = hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    response = {
        "fingerprint": fingerprint,
        "result_id": result_id,
        "incoming_result_id": incoming_id,
        "incoming_label": lineage["response"]["incoming_label"],
        "revision": revision,
        "decisions": evidence["decisions"],
        "summary": reviewed["summary"],
        "passages": reviewed["passages"],
        "truncated": reviewed["truncated"],
    }
    return {"lineage": lineage, "reviewed": reviewed, "evidence": evidence, "response": response}


def preview(store, recording_id, result_id, incoming_id, revision, decisions=None):
    with store.connection() as db:
        db.execute("BEGIN")
        return _plan(
            store,
            db,
            recording_id,
            result_id,
            incoming_id,
            revision,
            [] if decisions is None else decisions,
        )["response"]


def apply(
    store,
    recording_id,
    result_id,
    incoming_id,
    revision,
    decisions,
    fingerprint,
    reason=None,
    *,
    working_choice=None,
):
    validate_reason(reason)
    if not decisions:
        raise ValueError("Choose at least one passage group to record a review decision.")
    with store.snapshot_lock:
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            plan = _plan(store, db, recording_id, result_id, incoming_id, revision, decisions)
            if not isinstance(fingerprint, str) or not hmac.compare_digest(
                fingerprint, plan["response"]["fingerprint"]
            ):
                raise ValueError("The conflict review changed in another window. Preview it again.")
            lineage, reviewed = plan["lineage"], plan["reviewed"]
            version = append_edit(
                store,
                db,
                lineage["current"],
                result_id,
                lineage["ours_head"],
                reviewed["segments"],
                reviewed["speakers"],
                action="result_conflict_review",
                context={"conflict_review": plan["evidence"]},
                reason=reason,
                working_choice=working_choice,
            )
        store.snapshot_project(lineage["current"]["project_id"])
        return {
            "recording": store.recording(recording_id),
            "version_id": version["id"],
            "summary": reviewed["summary"],
        }


def verify_historical(row, captured):
    """Reconstruct the complete successor using budgeted immutable report snapshots only."""
    evidence = conflict_review_metadata(row["context"].get("conflict_review"))
    try:
        previous = captured[row["predecessor_id"]]
        parents = {
            name: captured[evidence[name]["version_id"]]
            for name in ("base", "incoming", "creation")
        }
        if (
            previous["result_id"] != row["result_id"]
            or previous["recording_id"] != row["recording_id"]
            or previous["sequence"] + 1 != row["sequence"]
        ):
            raise ValueError()
        for name, parent in parents.items():
            reference = evidence[name]
            if (
                parent["result_id"] != reference["result_id"]
                or parent["payload_sha256"] != reference["payload_sha256"]
                or parent["recording_id"] != row["recording_id"]
            ):
                raise ValueError()
        if (
            parents["base"]["result_id"] != row["result_id"]
            or parents["incoming"]["result_id"] == row["result_id"]
            or parents["creation"]["result_id"] != parents["incoming"]["result_id"]
            or parents["creation"]["sequence"] != 1
            or parents["creation"]["source_version_id"] != parents["base"]["id"]
            or parents["incoming"]["source_version_id"] != parents["base"]["id"]
            or row["source_version_id"] != previous["source_version_id"]
        ):
            raise ValueError()
        reviewed = review_payload(
            parents["base"]["payload"],
            previous["payload"],
            parents["incoming"]["payload"],
            parents["creation"]["payload"],
            evidence["decisions"],
        )
        encoded, digest = encode_payload(reviewed["segments"], reviewed["speakers"])
        if (
            digest != row["payload_sha256"]
            or json.loads(encoded) != row["payload"]
            or reviewed["summary"] != evidence["summary"]
        ):
            raise ValueError()
    except (KeyError, TypeError, AttributeError, ValueError, RecursionError):
        raise ValueError("The saved conflict review failed its ancestry check.") from None
    row["conflict_review_evidence"] = evidence
    row["conflict_review_ordinals"] = {
        passage["id"]: index + 1 for index, passage in enumerate(previous["payload"]["segments"])
    }


def public_projection(row, version_aliases):
    evidence = row["conflict_review_evidence"]
    ordinals = row["conflict_review_ordinals"]
    return {
        "format": FORMAT,
        **{
            name + "_version": version_aliases[evidence[name]["version_id"]]
            for name in ("base", "incoming", "creation")
        },
        "decisions": [
            {
                "passage": f"passage-{ordinals[item['passage_id']]:03}"
                if item["passage_id"] in ordinals
                else "removed-passage",
                "group": item["group"],
                "choice": item["choice"],
            }
            for item in evidence["decisions"]
        ],
        "summary": evidence["summary"],
    }
