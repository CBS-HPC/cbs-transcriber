"""Offline research prototype: exact evidence, explicit human decisions, no model transport."""

import json
import re

from .history import text_hash

CODEBOOK_FORMAT = "cbs-topic-codebook-v1"
ANNOTATION_FORMAT = "cbs-topic-annotation-v1"
IMPORT_FORMAT = "cbs-topic-suggestions-v1"
EXPORT_FORMAT = "cbs-topic-review-export-v1"
POLARITIES = {"positive", "negative", "mixed", "neutral", "uncertain"}
ATTRIBUTIONS = {"self", "reported_other", "unclear"}
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
MAX_PASSAGES = 1000


def canonical(value):
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        return encoded, text_hash(encoded)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("Use valid, finite Unicode JSON fields.") from exc


def verified_document(text, digest, format_name):
    try:
        if text_hash(text) != digest:
            raise ValueError
        payload = json.loads(text)
        if payload.get("format") != format_name or canonical(payload)[0] != text:
            raise ValueError
    except (AttributeError, TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("The saved annotation evidence failed its integrity check.") from exc
    return payload


def _object(value, fields, message):
    if not isinstance(value, dict) or value.keys() - fields:
        raise ValueError(message)


def plain_text(value, maximum, *, required=False):
    if not isinstance(value, str) or len(value) > maximum or required and not value.strip():
        raise ValueError("Use bounded plain-text annotation fields.")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("Use valid Unicode annotation text.") from exc
    return value


def request_identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
        raise ValueError("Use a bounded request identifier.")
    return value


def codebook_payload(value, previous=None):
    _object(value, {"format", "approved", "topics"}, "Unsupported codebook fields.")
    if (
        value.get("format", CODEBOOK_FORMAT) != CODEBOOK_FORMAT
        or type(value.get("approved")) is not bool
    ):
        raise ValueError("Use the supported codebook format and explicit researcher approval.")
    topics = value.get("topics")
    if not isinstance(topics, list) or not 1 <= len(topics) <= 100:
        raise ValueError("A codebook needs between 1 and 100 topics.")
    result, seen = [], set()
    for item in topics:
        _object(
            item,
            {"id", "name", "definition", "include", "exclude", "examples", "retired"},
            "Unsupported topic definition fields.",
        )
        identifier = item.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", identifier):
            raise ValueError("Use stable topic identifiers of at most 64 letters, digits or ._-. ")
        if identifier in seen:
            raise ValueError("Each topic identifier must occur once.")
        seen.add(identifier)
        topic = {
            "id": identifier,
            "name": plain_text(item.get("name"), 200, required=True),
            "definition": plain_text(item.get("definition"), 4000, required=True),
            "retired": item.get("retired", False),
        }
        if type(topic["retired"]) is not bool:
            raise ValueError("Topic retirement must be true or false.")
        for field in ("include", "exclude", "examples"):
            entries = item.get(field, [])
            if not isinstance(entries, list) or len(entries) > 50:
                raise ValueError("Limit each topic rule/example list to 50 entries.")
            topic[field] = [plain_text(text, 2000, required=True) for text in entries]
        result.append(topic)
    if previous and {item["id"] for item in previous["topics"]} - seen:
        raise ValueError(
            "Keep stable topic IDs; retire topics instead of removing their definitions."
        )
    payload = {"format": CODEBOOK_FORMAT, "approved": value["approved"], "topics": result}
    if len(canonical(payload)[0].encode("utf-8")) > 256 * 1024:
        raise ValueError("The codebook exceeds the 256 KiB prototype limit.")
    return payload


def source_passages(source):
    passages = {}
    for passage in source["segments"]:
        if not isinstance(passage, dict):
            raise ValueError("The frozen source needs literal passage objects.")
        identifier = passage.get("id")
        if (
            not isinstance(identifier, str)
            or identifier in passages
            or not isinstance(passage.get("text"), str)
        ):
            raise ValueError("The frozen source needs unique passage IDs and literal text.")
        if passage.get("speaker") is not None and not isinstance(passage["speaker"], str):
            raise ValueError("Frozen speaker labels must be text identifiers.")
        passages[identifier] = passage
    return passages


def selected_passages(source, passage_ids):
    passages = source_passages(source)
    if passage_ids is None:
        passage_ids = list(passages)
    if (
        not isinstance(passage_ids, list)
        or not 1 <= len(passage_ids) <= MAX_PASSAGES
        or any(not isinstance(item, str) or item not in passages for item in passage_ids)
        or len(set(passage_ids)) != len(passage_ids)
    ):
        raise ValueError("Choose between 1 and 1,000 distinct passages from the frozen source.")
    return list(passage_ids)


def annotation_payload(value, source, codebook, passage_ids):
    _object(
        value,
        {"format", "topic_id", "polarity", "attribution", "evidence", "uncertainty_reason"},
        "Unsupported annotation fields.",
    )
    if value.get("format", ANNOTATION_FORMAT) != ANNOTATION_FORMAT:
        raise ValueError("Use the supported annotation format.")
    topics = {topic["id"]: topic for topic in codebook["topics"]}
    topic_id = value.get("topic_id")
    if not isinstance(topic_id, str) or topic_id not in topics or topics[topic_id]["retired"]:
        raise ValueError("Choose an active topic from this run's frozen codebook.")
    polarity, attribution = value.get("polarity"), value.get("attribution")
    if not isinstance(polarity, str) or polarity not in POLARITIES:
        raise ValueError("Choose a supported topic evaluation.")
    if not isinstance(attribution, str) or attribution not in ATTRIBUTIONS:
        raise ValueError("Choose self, reported_other or unclear attribution.")
    uncertainty = plain_text(value.get("uncertainty_reason", ""), 2000)
    if polarity == "uncertain" and not uncertainty.strip():
        raise ValueError("Explain why the topic evaluation is uncertain.")
    evidence = value.get("evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 32:
        raise ValueError("Supply between 1 and 32 exact evidence spans.")
    passages, speakers, spans = source_passages(source), set(), []
    for span in evidence:
        _object(
            span,
            {"passage_id", "start_char", "end_char", "exact_text"},
            "Evidence accepts only passage ID, code-point offsets and exact text.",
        )
        identifier, start, end = (
            span.get("passage_id"),
            span.get("start_char"),
            span.get("end_char"),
        )
        if (
            not isinstance(identifier, str)
            or identifier not in passage_ids
            or identifier not in passages
        ):
            raise ValueError("Evidence must use a passage selected in this run.")
        passage = passages[identifier]
        quote = plain_text(span.get("exact_text"), 8000, required=True)
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(passage["text"])
            or passage["text"][start:end] != quote
        ):
            raise ValueError("Evidence offsets must match the exact frozen Unicode passage text.")
        speakers.add(passage.get("speaker"))
        spans.append(dict(span))
    if len(speakers) > 1 and attribution != "unclear":
        raise ValueError("Multi-speaker evidence needs unclear attribution in this prototype.")
    payload = {
        "format": ANNOTATION_FORMAT,
        "topic_id": topic_id,
        "polarity": polarity,
        "attribution": attribution,
        "evidence": spans,
        "uncertainty_reason": uncertainty,
    }
    if len(canonical(payload)[0].encode("utf-8")) > 128 * 1024:
        raise ValueError("An annotation exceeds the 128 KiB prototype limit.")
    return payload


def import_bundle(value):
    _object(
        value,
        {
            "format",
            "result_id",
            "source_version_id",
            "source_payload_sha256",
            "codebook_version_id",
            "codebook_payload_sha256",
            "passage_ids",
            "suggestions",
            "producer",
        },
        "Unsupported suggestion import fields.",
    )
    if value.get("format") != IMPORT_FORMAT:
        raise ValueError("Use the cbs-topic-suggestions-v1 import format.")
    if len(canonical(value)[0].encode("utf-8")) > MAX_BUNDLE_BYTES:
        raise ValueError("Suggestion imports are limited to 2 MiB.")
    for key in ("result_id", "source_version_id", "codebook_version_id"):
        plain_text(value.get(key), 128, required=True)
    for key in ("source_payload_sha256", "codebook_payload_sha256"):
        if not isinstance(value.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", value[key]):
            raise ValueError("Supply exact source and codebook SHA-256 hashes.")
    suggestions = value.get("suggestions")
    if not isinstance(suggestions, list) or len(suggestions) > 1000:
        raise ValueError("An import may contain at most 1,000 suggestions.")
    seen = set()
    for item in suggestions:
        _object(item, {"id", "annotation"}, "Unsupported suggestion item fields.")
        identity = request_identity(item.get("id"))
        if identity in seen:
            raise ValueError("Imported suggestion IDs must be unique.")
        seen.add(identity)
        if not isinstance(item.get("annotation"), dict):
            raise ValueError("Each imported suggestion needs an annotation object.")
    producer = value.get("producer", {})
    _object(producer, {"model", "revision"}, "Only declared model/revision fields may be imported.")
    for entry in producer.values():
        plain_text(entry, 200, required=True)
    return value


def export_bytes(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
