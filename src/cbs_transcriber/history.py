"""Exact local version encoding and bounded, allowlisted observation metadata."""

import hashlib
import json
import math
import re
import threading
from collections import OrderedDict
from datetime import datetime

FORMAT = "cbs-result-snapshot-v1"
MAX_IMPORT_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_OBSERVATION_BYTES = 64
EDIT_ACTIONS = {
    "word_correction",
    "word_timing_edit",
    "free_rewrite",
    "original_restore",
    "passage_speaker",
    "passage_merge",
    "passage_merge_undo",
    "passage_split",
    "passage_split_undo",
    "passage_delete",
    "passage_delete_undo",
    "speaker_rename",
    "manual_save",
    "version_restore",
    "result_merge",
    "result_conflict_review",
    "wording_adopt",
    "timing_realign",
}
EDIT_CONTEXT = {
    "segment_id",
    "word_index",
    "mode",
    "text_policy",
    "split_policy",
    "restored_from_version_id",
    "deleted_version_id",
    "direction",
    "cursor",
    "split_time",
    "adjacent_segment_id",
    "job_id",
}


def validate_import_artifact_evidence(value):
    """Validate byte evidence; schema1/2 selects the frozen legacy/strict import parser."""
    fields = ("schema", "role", "source_id", "sha256", "bytes", "format")
    if (
        not isinstance(value, dict)
        or set(value) != set(fields)
        or type(value.get("schema")) is not int
        or value["schema"] not in (1, 2)
        or value.get("role") != "transcript_import"
        or not isinstance(value.get("source_id"), str)
        or not re.fullmatch(r"[a-f0-9]{32}", value["source_id"])
        or not isinstance(value.get("sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])
        or type(value.get("bytes")) is not int
        or not 1 <= value["bytes"] <= MAX_IMPORT_ARTIFACT_BYTES
        or value.get("format") not in ("json", "csv")
    ):
        raise ValueError("The imported transcript artifact evidence is invalid.")
    return {field: value[field] for field in fields}


def encode_payload(segments, speakers):
    if not isinstance(segments, list) or not isinstance(speakers, dict):
        raise ValueError("A result snapshot requires passages and speaker labels.")
    payload = json.dumps(
        {"format": FORMAT, "segments": segments, "speakers": speakers},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def decode_payload(payload, digest):
    """Refuse damaged historical bytes rather than exporting them under a stale hash."""
    try:
        if not isinstance(payload, str) or text_hash(payload) != digest:
            raise ValueError
        decoded = json.loads(payload)
        if not isinstance(decoded, dict) or decoded.get("format") != FORMAT:
            raise ValueError
        if encode_payload(decoded["segments"], decoded["speakers"])[0] != payload:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("The saved research version failed its integrity check.") from exc
    return decoded


# Only fully validated or canonical JSON-origin payloads enter this bounded process-local
# memo. Every use still hashes current bytes; it grants no ownership or version authority.
_VERIFIED_PAYLOADS = OrderedDict()
_VERIFIED_PAYLOADS_LOCK = threading.Lock()
_VERIFIED_PAYLOADS_LIMIT = 512
_SERIALIZED_PAYLOAD_BINDINGS = OrderedDict()
_SERIALIZED_PAYLOAD_BINDINGS_LIMIT = 512


def _remember_verified_payload(digest):
    with _VERIFIED_PAYLOADS_LOCK:
        _VERIFIED_PAYLOADS[digest] = True
        _VERIFIED_PAYLOADS.move_to_end(digest)
        while len(_VERIFIED_PAYLOADS) > _VERIFIED_PAYLOADS_LIMIT:
            _VERIFIED_PAYLOADS.popitem(last=False)


def encode_serialized_payload(segments, speakers):
    """Encode stored JSON and retain its format fact, never decoded objects or authority.

    Parsing immutable stored strings gives ordinary JSON containers with string keys.
    Arbitrary Python dictionaries cannot seed this fact: numeric keys can sort differently
    after a JSON round-trip. The general encoder keeps its exact legacy behavior.
    """
    payload, digest = encode_payload(json.loads(segments), json.loads(speakers))
    binding = _serialized_payload_binding(segments, speakers, digest)
    _remember_verified_payload(digest)
    if binding is not None:
        with _VERIFIED_PAYLOADS_LOCK:
            _SERIALIZED_PAYLOAD_BINDINGS[binding] = True
            _SERIALIZED_PAYLOAD_BINDINGS.move_to_end(binding)
            while len(_SERIALIZED_PAYLOAD_BINDINGS) > _SERIALIZED_PAYLOAD_BINDINGS_LIMIT:
                _SERIALIZED_PAYLOAD_BINDINGS.popitem(last=False)
    return payload, digest


def _serialized_payload_binding(segments, speakers, digest):
    # SQL text mirrors are immutable. Unsupported nontext values retain the cold path.
    if isinstance(segments, str) and isinstance(speakers, str):
        try:
            return text_hash(segments), text_hash(speakers), digest
        except (TypeError, ValueError):
            # Preserve the original JSON/encode outcome when raw UTF8 cannot be hashed.
            pass
    return None


def serialized_payload_matches(segments, speakers, payload, digest):
    """Prove a current byte binding, retaining the exact canonical fallback on a miss.

    Facts contain three digests only. A hit freshly hashes both mirrors and the actual
    head bytes, and requires the head's stored hash too. Caller-owned database identity,
    revision, source and Working checks remain independent of this format-only memo.
    """
    try:
        actual_digest = text_hash(payload) if isinstance(payload, str) else None
    except (TypeError, ValueError):
        actual_digest = None
    binding = _serialized_payload_binding(segments, speakers, actual_digest)
    if binding is not None and actual_digest == digest:
        with _VERIFIED_PAYLOADS_LOCK:
            found = binding in _SERIALIZED_PAYLOAD_BINDINGS
            if found:
                _SERIALIZED_PAYLOAD_BINDINGS.move_to_end(binding)
        if found:
            _remember_verified_payload(actual_digest)
            return True
    expected_payload, expected_digest = encode_serialized_payload(segments, speakers)
    return payload == expected_payload and digest == expected_digest


def verify_payload(payload, digest):
    """Validate unchanged historical bytes without decoding them repeatedly."""
    try:
        if not isinstance(payload, str) or text_hash(payload) != digest:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("The saved research version failed its integrity check.") from exc
    with _VERIFIED_PAYLOADS_LOCK:
        if digest in _VERIFIED_PAYLOADS:
            _VERIFIED_PAYLOADS.move_to_end(digest)
            return
    decode_payload(payload, digest)
    _remember_verified_payload(digest)


def validate_reason(reason):
    if reason is not None and (not isinstance(reason, str) or len(reason) > 2000):
        raise ValueError("A save reason must be plain text of at most 2,000 characters.")
    return reason


def validate_revision(revision):
    if type(revision) is not int or not 0 <= revision < 2**63:
        raise ValueError("Use a valid saved revision.")


def validate_version_observation(sequence, observed_at):
    """Keep preserved version ordinals/times typed, bounded and free of arbitrary metadata."""
    try:
        if (
            type(sequence) is not int
            or sequence < 1
            or not isinstance(observed_at, str)
            or len(observed_at.encode("utf-8")) > MAX_OBSERVATION_BYTES
            or datetime.fromisoformat(observed_at).tzinfo is None
        ):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("The saved research observation metadata is invalid.") from None


def edit_metadata(action, context, reason, *, notes=False):
    allowed = {"notes_save"} if notes else EDIT_ACTIONS
    if not isinstance(action, str) or action not in allowed:
        raise ValueError("Choose a supported save action.")
    validate_reason(reason)
    if context is None:
        context = {}
    if not isinstance(context, dict) or context.keys() - EDIT_CONTEXT:
        raise ValueError("Unsupported save context.")
    for key, value in context.items():
        if key == "text_policy":
            if action != "free_rewrite" or value not in {
                "conservative-word-map-v1",
                "permissive-word-map-v2",
            }:
                raise ValueError("Use a supported Text-save timing policy.")
        elif key == "split_policy":
            if action != "passage_split" or value != "manual-word-boundary-v2":
                raise ValueError("Use a supported passage-split policy.")
        elif key in {"word_index", "cursor"}:
            if type(value) is not int or not 0 <= value < 2**63:
                raise ValueError("Use a valid word index.")
        elif key == "split_time":
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Use a finite nonnegative audio split time.")
        elif not isinstance(value, str) or len(value) > 200:
            raise ValueError("Use a bounded text identifier in save context.")
    return dict(context)


def _version_reference(reference, kind):
    if (
        not isinstance(reference, dict)
        or set(reference) != {"result_id", "version_id", "payload_sha256"}
        or any(
            not isinstance(reference.get(key), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", reference[key])
            for key in ("result_id", "version_id")
        )
        or not isinstance(reference.get("payload_sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", reference["payload_sha256"])
    ):
        raise ValueError(f"The saved {kind} evidence is invalid.")
    return dict(reference)


def wording_adoption_metadata(value):
    """Validate an exact accepted wording source, without names, text or private metadata."""
    passage_fields = ("target_passage_id", "source_passage_id")
    hash_fields = ("target_text_sha256", "source_text_sha256")
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "source", *passage_fields, *hash_fields}
        or value.get("format") != "cbs-wording-adoption-v1"
        or any(
            not isinstance(value.get(key), str)
            or not 0 < len(value[key]) <= 200
            or any(ord(char) < 32 for char in value[key])
            for key in passage_fields
        )
        or any(
            not isinstance(value.get(key), str) or not re.fullmatch(r"[a-f0-9]{64}", value[key])
            for key in hash_fields
        )
    ):
        raise ValueError("The saved wording adoption evidence is invalid.")
    return {**value, "source": _version_reference(value["source"], "wording adoption")}


def merge_metadata(value):
    """Validate server-recorded merge parents without accepting text or private metadata."""
    references = ("base", "incoming", "creation")
    counts = {
        "updated_passages",
        "timing_updates",
        "speaker_updates",
        "conflicts",
        "skipped",
        "unchanged",
    }
    if (
        not isinstance(value, dict)
        or set(value) != {"format", *references, "summary"}
        or value.get("format") != "cbs-enhancement-merge-v1"
    ):
        raise ValueError("The saved merge evidence is invalid.")
    output = {"format": value["format"]}
    for name in references:
        output[name] = _version_reference(value[name], "merge")
    summary = value["summary"]
    if (
        not isinstance(summary, dict)
        or set(summary) != counts
        or any(type(count) is not int or not 0 <= count <= 100_000 for count in summary.values())
    ):
        raise ValueError("The saved merge evidence is invalid.")
    output["summary"] = dict(summary)
    return output


def conflict_review_metadata(value):
    """Validate explicit group decisions; never accept a client-authored merged payload."""
    from .result_conflicts import FORMAT, SUMMARY_FIELDS, validate_decisions

    if (
        not isinstance(value, dict)
        or set(value) != {"format", "base", "incoming", "creation", "decisions", "summary"}
        or value.get("format") != FORMAT
    ):
        raise ValueError("The saved conflict review evidence is invalid.")
    summary = value["summary"]
    if (
        not isinstance(summary, dict)
        or set(summary) != set(SUMMARY_FIELDS)
        or any(type(count) is not int or not 0 <= count <= 100_000 for count in summary.values())
    ):
        raise ValueError("The saved conflict review evidence is invalid.")
    return {
        "format": FORMAT,
        **{
            name: _version_reference(value[name], "conflict review")
            for name in ("base", "incoming", "creation")
        },
        "decisions": validate_decisions(value["decisions"]),
        "summary": dict(summary),
    }


def automatic_language_metadata(source):
    """Revalidate paired Auto observations, without runtime/files or mixed decoder origins."""
    if (
        not isinstance(source, dict)
        or "language_decision" not in source
        or "language_evidence" in source
    ):
        return {}
    options = source.get("options")
    if ("language" in source and source["language"] != "auto") or (
        isinstance(options, dict) and "language" in options and options["language"] != "auto"
    ):
        return {}
    from .enhancement_worker import validate_language_decision, validate_language_outcome

    try:
        decision = validate_language_decision(source["language_decision"])
    except (ValueError, TypeError, KeyError, RecursionError):
        return {}
    output = {"language_decision": decision}
    align = options.get("align") if isinstance(options, dict) else None
    if "alignment_outcome" in source:
        try:
            output["alignment_outcome"] = validate_language_outcome(
                source["alignment_outcome"], decision, align=align if type(align) is bool else None
            )
        except (ValueError, TypeError, KeyError, RecursionError):
            pass  # Preserve valid language evidence, but never invent an alignment outcome.
    return output


def conversion_metadata(source):
    """Validate captured conversion against its ASR receipt, without files or model lookup."""
    if not isinstance(source, dict) or "asr_conversion" not in source:
        return {}
    from .danish_models import validate_evidence
    from .enhancement_worker import DANISH_CHOICES, validate_language_evidence

    try:
        if "language_decision" in source:
            return {}
        evidence = validate_language_evidence(source.get("language_evidence"))
        if len(evidence) != 1:
            return {}
        entry = evidence[0]
        pin = entry["model"]
        choice = pin["choice"]
        if (
            choice not in DANISH_CHOICES
            or entry["scope"] != "recording"
            or entry["requested"] != "da"
            or entry["window"]["start"] != 0
            or entry["window"]["end"] > 6 * 3600
            or source.get("operation") == "retranscription"
            or ("model" in source and source["model"] != choice)
            or ("language" in source and source["language"] != "da")
        ):
            return {}
        options = source.get("options")
        if isinstance(options, dict) and any(
            key in options and (type(options[key]) is not type(value) or options[key] != value)
            for key, value in (
                ("language", "da"),
                ("whisper_model", choice),
                ("transcribe", True),
                ("retranscribe", False),
            )
        ):
            return {}
        revisions = source.get("model_revisions")
        if isinstance(revisions, dict) and any(
            key in revisions
            and revisions[key] != {name: pin[name] for name in ("repo", "revision")}
            for key in (choice, "whisper/" + choice)
        ):
            return {}
        receipt = validate_evidence(
            source["asr_conversion"],
            choice=choice,
            model=(pin["repo"], pin["revision"]),
            request_sha256=entry["request_sha256"],
            input_sha256=entry["input_sha256"],
        )
    except (ValueError, TypeError, KeyError, RecursionError):
        return {}
    return {"asr_conversion": receipt}


def processing_metadata(source):
    """Only captured model/method evidence; never endpoint, secret or runtime identity."""
    if not isinstance(source, dict):
        return {}
    result = {}
    for key in (
        "provider_type",
        "model",
        "language",
        "capability",
        "execution",
        "engine",
        "version",
    ):
        value = source.get(key)
        if isinstance(value, str) and len(value) <= 200 and not any(ord(c) < 32 for c in value):
            result[key] = value
    # Distinguish passage repairs from whole-result enhancement without copying passage IDs.
    if source.get("operation") == "retranscription" and isinstance(source.get("segment_ids"), list):
        result["scope"] = "passage_retranscription"
        result["passage_count"] = len(source["segment_ids"])
    elif isinstance(source.get("segment_ids"), list):
        result["scope"] = "passage_repairs"
        result["passage_count"] = len(source["segment_ids"])
    elif source.get("segment_id") is not None:
        result["scope"] = "passage_repair"
        result["passage_count"] = 1
    elif source.get("kind") == "enhancement":
        result["scope"] = "whole_result"
    revisions = source.get("model_revisions")
    if isinstance(revisions, dict):
        models = {}
        for key, value in revisions.items():
            if not isinstance(key, str) or len(key) > 200 or not isinstance(value, dict):
                continue
            item = {
                field: value[field]
                for field in ("repo", "revision")
                if isinstance(value.get(field), str) and len(value[field]) <= 200
            }
            if item:
                models[key] = item
        if models:
            result["model_revisions"] = models
    options = source.get("options")
    if isinstance(options, dict):
        safe = {}
        for key in (
            "device",
            "language",
            "execution",
            "align",
            "diarize",
            "min_score",
            "min_speakers",
            "max_speakers",
        ):
            value = options.get(key)
            if value is None or isinstance(value, bool):
                if key in options:
                    safe[key] = value
            elif isinstance(value, str) and len(value) <= 200:
                safe[key] = value
            elif type(value) in (int, float) and math.isfinite(value):
                safe[key] = value
        if safe:
            result["options"] = safe
        if options.get("timing_policy") in ("legacy-score-filter", "retain-estimates"):
            result.setdefault("options", {})["timing_policy"] = options["timing_policy"]
    evidence = source.get("language_evidence")
    if evidence is not None and "language_decision" not in source:
        from .enhancement_worker import validate_language_evidence

        try:
            result["language_evidence"] = validate_language_evidence(evidence)
        except (ValueError, TypeError, KeyError, RecursionError):
            # Preserve untrusted source metadata separately, never publish it as run evidence.
            pass
    result.update(automatic_language_metadata(source))
    result.update(conversion_metadata(source))
    return result
