"""Model review hints for results created before hints were kept, rebuilt from preserved output.

Hints are derived only from the exact raw responses or enhancement outputs that produced a
result, and only when the rebuilt passages match the result's first saved version text for
text. Anything else is recorded as unavailable rather than guessed.
"""

import hashlib
import json
from bisect import bisect_left

from .history import decode_payload
from .transcripts import (
    MAX_TRANSCRIPT_BYTES,
    combine_chunks,
    hints_document,
    parse_transcript,
    pop_hints,
)

UNAVAILABLE = {"version": 1, "origin": "unavailable", "passages": {}}


def hints_for(store, recording_id, result_id):
    """Cached hints, or a one-time rebuild that is then cached (including "unavailable")."""
    document = store.result_hints(recording_id, result_id)
    if document is None:
        result = store.result(recording_id, result_id, include_trashed=True)
        try:
            document = rebuild(store, result) or UNAVAILABLE
        except FileNotFoundError:
            document = UNAVAILABLE
        except OSError:
            # A transient lock (sync client, antivirus) must not hide hints for good.
            return UNAVAILABLE
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            document = UNAVAILABLE
        store.save_result_hints(result_id, document)
        document = store.result_hints(recording_id, result_id) or document
    return document


def current_hints(store, recording_id, result_id):
    """Only hints whose passage still has exactly the text the model produced."""
    document = hints_for(store, recording_id, result_id)
    result = store.result(recording_id, result_id, include_trashed=True)
    segments = {segment["id"]: segment for segment in result["segments"]}
    passages = {
        identifier: _current_alignment_hints(value, segments[identifier])
        for identifier, value in document["passages"].items()
        if identifier in segments
        and hashlib.sha256(segments[identifier]["text"].encode("utf-8")).hexdigest()
        == value["text_sha256"]
    }
    return {
        "origin": document["origin"],
        "passages": passages,
        "edited_passages": len(document["passages"]) - len(passages),
    }


def _current_alignment_hints(value, segment):
    """Do not show source alignment fit for manually moved audio intervals."""
    if value.get("word_kind") != "alignment":
        return value
    try:
        words = segment.get("words", [])
        if not isinstance(words, list) or any(not isinstance(word, dict) for word in words):
            raise ValueError
        marked = [
            word for word in words if "manual_timing" in word or "alignment_hints_stale" in word
        ]
        if not marked:
            return value
        if any(
            key in word and word[key] is not True
            for word in marked
            for key in ("manual_timing", "alignment_hints_stale")
        ):
            raise ValueError
        text = segment["text"]
        spans = sorted((word["char_start"], word["char_end"]) for word in marked)
        if any(
            type(left) is not int or type(right) is not int or not 0 <= left < right <= len(text)
            for left, right in spans
        ):
            raise ValueError
        merged = []
        for left, right in spans:
            if merged and left <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], right))
            else:
                merged.append((left, right))
        starts = [left for left, _ in merged]
        hints = value.get("words", [])
        if not isinstance(hints, list):
            raise ValueError
        kept = []
        for hint in hints:
            if (
                not isinstance(hint, list)
                or len(hint) != 3
                or type(hint[0]) is not int
                or type(hint[1]) is not int
                or hint[0] >= hint[1]
            ):
                raise ValueError
            index = bisect_left(starts, hint[1]) - 1
            if index < 0 or merged[index][1] <= hint[0]:
                kept.append(hint)
    except (KeyError, TypeError, ValueError):
        kept = []  # Corrupt span evidence cannot prove any alignment hint is current.
    return {**value, "words": kept}


def first_version_segments(store, recording_id, result_id):
    rows = store.all(
        "SELECT payload,payload_sha256 FROM result_versions WHERE result_id=? "
        "ORDER BY sequence LIMIT 1",
        (result_id,),
    )
    if not rows:
        return None
    return decode_payload(rows[0]["payload"], rows[0]["payload_sha256"])["segments"]


def rebuild(store, result):
    if not result.get("job_id"):
        return None
    job = store.job(result["job_id"])
    if job["recording_id"] != result["recording_id"]:
        return None
    if job["kind"] == "transcribe":
        derived = _transcription(store, job)
    elif job["kind"] == "enhance":
        derived = _enhancement(store, job)
    else:
        return None
    original = first_version_segments(store, result["recording_id"], result["id"])
    if not derived or not original or len(derived) != len(original):
        return None
    for rebuilt, saved in zip(derived, original, strict=True):
        if rebuilt["id"] != saved["id"] or rebuilt["text"] != saved["text"]:
            return None
    return hints_document(pop_hints(derived), original, "rebuilt")


def _read(path):
    if not path.is_file() or path.stat().st_size > MAX_TRANSCRIPT_BYTES:
        return None
    return path.read_bytes()


def _transcription(store, job):
    directory = store.directory(job["recording_id"])
    settings = job["params"].get("settings", {})
    if settings.get("provider_type") == "ucloud":
        data = _read(directory / ("ucloud-" + job["id"] + ".json"))
        return parse_transcript(data, "transcript.json") if data else None
    from .providers import normalize_response

    folder = directory / "chunks" / job["id"]
    chunks = []
    for position, part in enumerate(job["chunks"]):
        index = part.get("index", position)
        data = _read(folder / f"response-{index:04d}.json")
        if data is None:
            return None
        chunks.append(
            {
                "index": index,
                "offset": part["offset"],
                "segments": normalize_response(json.loads(data)),
            }
        )
    if not chunks:
        return None
    return combine_chunks(sorted(chunks, key=lambda part: part["index"]))


def _enhancement(store, job):
    from .enhancement import EnhancementOptions, merge_output, read_output

    params = job["params"]
    if params.get("segment_id") is not None or params.get("segment_ids") is not None:
        return None  # Repairs combine several windows; their hints are kept from creation on.
    options = EnhancementOptions.model_validate(params["enhancement"])
    folder = store.directory(job["recording_id"]) / "enhancements" / job["id"]
    alignment = read_output(folder / "alignment.json") if options.align else None
    diarization = read_output(folder / "diarization.json") if options.diarize else None
    recording = store.recording(job["recording_id"])
    segments, _ = merge_output(
        params["source_snapshot"],
        alignment,
        diarization,
        options,
        recording["duration"],
        job["id"][:8],
    )
    return segments
