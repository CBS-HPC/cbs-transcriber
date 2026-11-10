"""Disposable synthetic design example, not an application anonymisation feature.

No file/library input, persistence, inference, providers or re-identification key. The
caller-owned objects below model a trusted source snapshot, not a public input parser.
"""

import hashlib
import json
import re
import unicodedata

RULESET = "synthetic-email-literal-v1"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)
BINDING_FIELDS = {"recording_id", "result_id", "version_id", "payload_sha256", "role_revision"}


def _verify(snapshot):
    payload, binding = snapshot["payload"], snapshot["binding"]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    if (
        set(binding) != BINDING_FIELDS
        or hashlib.sha256(encoded.encode("utf-8")).hexdigest() != binding["payload_sha256"]
        or payload["format"] != "cbs-result-snapshot-v1"
    ):
        raise ValueError("Source binding or literal payload changed.")
    return payload, binding


def span(snapshot, passage, start, end, category="manual"):
    """Model an exact source-relative suggestion/manual selection; offsets are code points."""
    payload, binding = _verify(snapshot)
    if (
        type(passage) is not int
        or not 0 <= passage < len(payload["segments"])
        or type(start) is not int
        or type(end) is not int
    ):
        raise ValueError("Invalid literal span coordinates.")
    text = payload["segments"][passage]["text"]
    if not 0 <= start < end <= len(text) or category not in {"email", "known", "manual"}:
        raise ValueError("Invalid literal span coordinates or category.")
    return {
        "source": dict(binding),
        "passage": passage,
        "start": start,
        "end": end,
        "quote": text[start:end],
        "category": category,
    }


def suggest(snapshot, known_literals=()):
    """Original-string matches only: no normalisation, fuzzy linking or auto approval."""
    payload, _ = _verify(snapshot)
    found = []
    for index, segment in enumerate(payload["segments"]):
        text = segment["text"]
        for match in EMAIL.finditer(text):
            found.append(span(snapshot, index, *match.span(), "email"))
        for literal in known_literals:
            if not isinstance(literal, str) or not literal:
                raise ValueError("Known literals must be nonempty text.")
            for match in re.finditer(re.escape(literal), text):

                def word_character(char):
                    return (
                        char.isalnum() or char == "_" or unicodedata.category(char).startswith("M")
                    )

                if (
                    match.start()
                    and word_character(text[match.start() - 1])
                    or match.end() < len(text)
                    and word_character(text[match.end()])
                ):
                    continue
                found.append(span(snapshot, index, *match.span(), "known"))
    return sorted(
        found, key=lambda hit: (hit["passage"], hit["start"], hit["end"], hit["category"])
    )


def derive(snapshot, expected_binding, spans, decisions, speaker_labels):
    """Construct a private receipt and separate allowlisted text-only derived preview."""
    payload, binding = _verify(snapshot)
    if binding != expected_binding:
        raise ValueError("Source version or Working designation changed.")
    if len(spans) != len(decisions):
        raise ValueError("Every suggestion needs one explicit decision.")
    replacements = {}
    for hit, decision in zip(spans, decisions, strict=True):
        if hit != span(snapshot, hit["passage"], hit["start"], hit["end"], hit["category"]):
            raise ValueError("Suggestion does not match the exact source.")
        if decision == {"action": "keep"}:
            continue
        if (
            set(decision) != {"action", "text"}
            or decision["action"] != "replace"
            or not isinstance(decision["text"], str)
            or not decision["text"].strip()
        ):
            raise ValueError("Choose explicit Keep or nonempty Replace text.")
        replacements.setdefault(hit["passage"], []).append((hit, decision["text"]))
    source_voices = list(dict.fromkeys(segment["speaker"] for segment in payload["segments"]))
    if set(speaker_labels) != set(source_voices) or any(
        not isinstance(label, str) or not label.strip() for label in speaker_labels.values()
    ):
        raise ValueError("Review every displayed speaker label.")
    aliases = {voice: f"voice-{index}" for index, voice in enumerate(source_voices, 1)}
    segments = []
    for index, segment in enumerate(payload["segments"]):
        cursor, pieces = 0, []
        for hit, replacement in sorted(
            replacements.get(index, []), key=lambda item: item[0]["start"]
        ):
            if hit["start"] < cursor:
                raise ValueError("Resolve overlapping replacements explicitly.")
            pieces.extend((segment["text"][cursor : hit["start"]], replacement))
            cursor = hit["end"]
        pieces.append(segment["text"][cursor:])
        segments.append(
            {"passage": index + 1, "text": "".join(pieces), "speaker": aliases[segment["speaker"]]}
        )
    public = {
        "format": "cbs-anonymisation-synthetic-demo-v1",
        "method": {"ruleset": RULESET, "review": "explicit-demo-decisions", "timing": "omitted"},
        "segments": segments,
        "speakers": {aliases[voice]: speaker_labels[voice] for voice in source_voices},
    }
    # Copy the private receipt; none of it is part of the shareable serializer below.
    private = json.loads(
        json.dumps(
            {
                "source": binding,
                "spans": spans,
                "decisions": decisions,
                "speaker_labels": speaker_labels,
                "ruleset": RULESET,
                "output_sha256": hashlib.sha256(
                    json.dumps(
                        public, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")
                ).hexdigest(),
            },
            ensure_ascii=False,
        )
    )
    return {"shareable": public, "private_receipt": private}


def synthetic_source():
    text = "🙂 SYNTHETIC_PERSON_ALPHA skrev til demo@example.invalid.\r\nBevar e\u0301 og Ø."
    payload = {
        "format": "cbs-result-snapshot-v1",
        "segments": [
            {
                "id": "HIDDEN_PASSAGE_ID",
                "text": text,
                "speaker": "HIDDEN_VOICE_ID",
                "start": 0,
                "end": 4,
                "words": [{"word": "HIDDEN_OLD_WORD"}],
                "original_text": "HIDDEN_ORIGINAL_TEXT",
            }
        ],
        "speakers": {"HIDDEN_VOICE_ID": "HIDDEN_SPEAKER_NAME"},
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "binding": {
            "recording_id": "HIDDEN_RECORDING_ID",
            "result_id": "HIDDEN_RESULT_ID",
            "version_id": "HIDDEN_VERSION_ID",
            "payload_sha256": digest,
            "role_revision": 3,
        },
        "payload": payload,
        "filename": "HIDDEN_FILENAME.wav",
        "notes": "HIDDEN_NOTES",
        "history": [{"text": "HIDDEN_HISTORY"}],
        "project": "HIDDEN_PROJECT",
        "collection": "HIDDEN_COLLECTION",
        "path": "HIDDEN_PATH",
        "label": "HIDDEN_RESULT_LABEL",
        "report_metadata": {"participant": "HIDDEN_REPORT_METADATA"},
        "media_url": "HIDDEN_MEDIA_URL",
    }


def demo():
    snapshot = synthetic_source()
    spans = suggest(snapshot, ("SYNTHETIC_PERSON_ALPHA",))
    decisions = [
        {"action": "replace", "text": "[PERSON]" if hit["category"] == "known" else "[EMAIL]"}
        for hit in spans
    ]
    return derive(
        snapshot, dict(snapshot["binding"]), spans, decisions, {"HIDDEN_VOICE_ID": "[VOICE_A]"}
    )


if __name__ == "__main__":
    # ASCII JSON escapes retain exact Unicode after decoding on every console encoding.
    print(json.dumps(demo()["shareable"], ensure_ascii=True, indent=2))
