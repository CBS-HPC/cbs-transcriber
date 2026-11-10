"""Offline reviewed-clip metrics with explicit timing, speaker and measurement evidence."""

import math
import re
from array import array
from statistics import fmean, median

from .evaluation import normalize, score
from .transcripts import word_mapping_current

MAX_WORDS = 2000
MAX_TEXT = 12000
ALIGNMENT = (
    "minimum-lexical-edits; traceback prefers exact/substitute, delete, insert; timing unused"
)


def number(value, *, positive=False):
    try:
        finite = math.isfinite(value) if isinstance(value, (int, float)) else False
    except OverflowError:
        finite = False
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not finite
        or value < 0
        or positive
        and value == 0
    ):
        raise ValueError("Measurements and intervals need finite nonnegative numbers.")
    return value


def annotated_words(text, supplied, duration):
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise ValueError("Use reviewed clips of at most 12,000 characters.")
    words = [{"word": w} for w in re.findall(r"\S+", text)] if supplied is None else supplied
    if not isinstance(words, list) or len(words) > MAX_WORDS:
        raise ValueError("Use clips of at most 2,000 annotated words per side.")
    clean, original = [], []
    for word in words:
        if not isinstance(word, dict) or not isinstance(word.get("word"), str):
            raise ValueError("Word annotations need word text.")
        token = word["word"].strip()
        if not token or re.search(r"\s", token):
            raise ValueError("Each annotation must represent one whitespace-delimited word.")
        original.append(token)
        a, b, speaker = word.get("start"), word.get("end"), word.get("speaker")
        for value in (a, b):
            if value is not None and number(value) > duration:
                raise ValueError("Word intervals must stay inside the actual clip.")
        if a is not None and b is not None and b <= a:
            raise ValueError("Annotated words need positive intervals or missing timing.")
        if speaker is not None and (not isinstance(speaker, str) or not 0 < len(speaker) <= 200):
            raise ValueError("Speaker annotations need bounded text IDs.")
        if normalized := normalize(token):
            clean.append({"word": normalized, "start": a, "end": b, "speaker": speaker})
    if normalize(" ".join(original)) != normalize(text):
        raise ValueError("Annotated words must preserve the complete corresponding text.")
    return clean


def from_saved_result(value):
    """Use current displayed timing only; free-text rewrites cannot restore stale intervals."""
    if "segments" not in value:
        return value
    segments = value["segments"]
    if not isinstance(segments, list) or len(segments) > MAX_WORDS:
        raise ValueError("Saved prediction needs a bounded list of passages.")
    text, words = [], []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            raise ValueError("Saved passages need text.")
        text.append(segment["text"])
        by_span = {}
        if segment.get("word_timing") is True or (
            segment.get("word_timing") is False and word_mapping_current(segment)
        ):
            previous_start, previous_end = -1, -1
            for word in segment.get("words", []):
                if not isinstance(word, dict):
                    raise ValueError("Saved words need objects.")
                left, right = word.get("char_start"), word.get("char_end")
                try:
                    start, end = number(word.get("start")), number(word.get("end"))
                    lower = number(segment["start"]) if segment.get("start") is not None else None
                    upper = number(segment["end"]) if segment.get("end") is not None else None
                except ValueError:
                    continue
                if (
                    type(left) is not int
                    or type(right) is not int
                    or not 0 <= left < right <= len(segment["text"])
                    or segment["text"][left:right] != word.get("word")
                    or end <= start
                    or start < previous_start
                    or end < previous_end
                    or (lower is not None and start < lower)
                    or (upper is not None and end > upper)
                ):
                    continue
                by_span[(left, right)] = word
                previous_start, previous_end = start, end
        for match in re.finditer(r"\S+", segment["text"]):
            found = by_span.get(match.span(), {})
            if found.get("word") != match[0]:
                found = {}
            speaker = segment.get("speaker")
            words.append(
                {
                    "word": match[0],
                    "start": found.get("start"),
                    "end": found.get("end"),
                    "speaker": speaker if speaker != "UNKNOWN" else None,
                }
            )
    return {
        **value,
        "text": "\n".join(text),
        "words": words,
        "contains_reviewed_edits": any(s.get("edited") for s in segments),
    }


def lexical_pairs(reference, hypothesis):
    """Compact exact edit-distance traceback; repeated-word ties are disclosed, not timed away."""
    first, second = [w["word"] for w in reference], [w["word"] for w in hypothesis]
    rows = [array("H", range(len(second) + 1))]
    for i, word in enumerate(first, 1):
        row = array("H", [i])
        for j, predicted in enumerate(second, 1):
            row.append(min(row[-1] + 1, rows[-1][j] + 1, rows[-1][j - 1] + (word != predicted)))
        rows.append(row)
    i, j, ties, pairs = len(first), len(second), 0, []
    while i or j:
        diagonal = rows[i - 1][j - 1] + (first[i - 1] != second[j - 1]) if i and j else math.inf
        delete = rows[i - 1][j] + 1 if i else math.inf
        insert = rows[i][j - 1] + 1 if j else math.inf
        cost = rows[i][j]
        ties += sum(value == cost for value in (diagonal, delete, insert)) > 1
        if diagonal == cost:
            if first[i - 1] == second[j - 1]:
                pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif delete == cost:
            i -= 1
        else:
            j -= 1
    return list(reversed(pairs)), ties


def measured(prediction, duration):
    values = prediction.get("measurements")
    if values is None:
        return {
            "elapsed_seconds": None,
            "time_scope": None,
            "real_time_factor": None,
            "peak_rss_bytes": None,
            "source": None,
            "memory_scope": None,
        }
    if (
        not isinstance(values, dict)
        or not isinstance(values.get("source"), str)
        or not values["source"].strip()
    ):
        raise ValueError("Reported measurements need explicit provenance in source.")
    elapsed, rss = values.get("elapsed_seconds"), values.get("peak_rss_bytes")
    if elapsed is not None:
        number(elapsed, positive=True)
        if not isinstance(values.get("time_scope"), str) or not values["time_scope"].strip():
            raise ValueError("Time measurements need a declared scope.")
    if rss is not None:
        if not isinstance(rss, int) or isinstance(rss, bool):
            raise ValueError("Peak RSS must be an integer byte count or missing.")
        number(rss, positive=True)
        if not isinstance(values.get("memory_scope"), str) or not values["memory_scope"].strip():
            raise ValueError("Memory measurements need a declared scope.")
    return {
        "elapsed_seconds": elapsed,
        "time_scope": values.get("time_scope"),
        "real_time_factor": elapsed / duration if elapsed is not None else None,
        "peak_rss_bytes": rss,
        "source": values["source"],
        "memory_scope": values.get("memory_scope"),
    }


def timing_summary(reference, matched, errors, within):
    return {
        "reference_words": reference,
        "matched_timed_words": matched,
        "coverage": matched / reference if reference else None,
        "mean_boundary_error_seconds": fmean(errors) if errors else None,
        "median_boundary_error_seconds": median(errors) if errors else None,
        "p90_boundary_error_seconds": sorted(errors)[math.ceil(0.9 * len(errors)) - 1]
        if errors
        else None,
        "within_250ms_words": within,
        "within_250ms_fraction": within / matched if matched else None,
    }


def speaker_summary(reference, scored, correct):
    return {
        "reference_words": reference,
        "scored_reference_words": scored,
        "correct_words": correct,
        "coverage": scored / reference if reference else None,
        "word_accuracy": correct / scored if scored else None,
        "metric": "speaker-attributed correctly matched reference words; explicit mapping; not DER",
    }


def performance_summary(rows):
    measured_time = [r for r in rows if r["measurements"]["elapsed_seconds"] is not None]
    measured_memory = [
        r["measurements"] for r in rows if r["measurements"]["peak_rss_bytes"] is not None
    ]
    elapsed = sum(r["measurements"]["elapsed_seconds"] for r in measured_time)
    duration = sum(r["duration_seconds"] for r in measured_time)
    time_scopes = sorted({r["measurements"]["time_scope"] for r in measured_time})
    scopes = sorted({r["memory_scope"] for r in measured_memory})
    return {
        "measured_time_clips": len(measured_time),
        "total_clips": len(rows),
        "elapsed_seconds": elapsed if measured_time else None,
        "real_time_factor": elapsed / duration if measured_time and len(time_scopes) == 1 else None,
        "time_scopes": time_scopes,
        "measured_memory_clips": len(measured_memory),
        "memory_scopes": scopes,
        "maximum_reported_peak_rss_bytes": max(r["peak_rss_bytes"] for r in measured_memory)
        if measured_memory and len(scopes) == 1
        else None,
        "note": "Reported measurements; inspect per-clip source/scope. No inferred GPU memory.",
    }


def score_reviewed(manifest, predictions):
    clips = manifest.get("clips")
    if not isinstance(clips, list) or not clips:
        raise ValueError("A reviewed suite needs clips.")
    if not isinstance(predictions, dict) or set(predictions) != {c["id"] for c in clips}:
        raise ValueError("Predictions must cover every reference clip exactly once.")
    if len({c["id"] for c in clips}) != len(clips):
        raise ValueError("Duplicate reference clip ID.")
    rows, timing_errors, texts = [], [], {}
    for clip in clips:
        prediction = predictions[clip["id"]]
        if not isinstance(prediction, dict) or not isinstance(prediction.get("text"), str):
            if isinstance(prediction, dict) and "segments" in prediction:
                prediction = from_saved_result(prediction)
            else:
                raise ValueError(
                    "Each prediction needs text/annotations or a saved transcript result."
                )
        language = clip.get("language", manifest.get("language", ""))
        if not isinstance(language, str):
            raise ValueError("Reviewed clips need language labels.")
        language = language.replace("-", "_").split("_")[0]
        if language not in {"da", "en"}:
            raise ValueError("Reviewed clips need Danish (da) or English (en) language labels.")
        duration = number(clip.get("duration_seconds"), positive=True)
        reference = annotated_words(clip["text"], clip.get("reference_words"), duration)
        reference_starts = [w["start"] for w in reference if w["start"] is not None]
        if reference_starts != sorted(reference_starts):
            raise ValueError("Reviewed reference intervals must follow transcript order.")
        hypothesis = annotated_words(prediction["text"], prediction.get("words"), duration)
        pairs, ties = lexical_pairs(reference, hypothesis)
        mapping = prediction.get("speaker_map")
        known = {w["speaker"] for w in reference if w["speaker"] is not None}
        predicted = {w["speaker"] for w in hypothesis if w["speaker"] is not None}
        if mapping is not None and (
            not isinstance(mapping, dict)
            or not set(mapping) <= predicted
            or any(not isinstance(v, str) or v not in known for v in mapping.values())
        ):
            raise ValueError(
                "Speaker mappings must connect predicted IDs to reviewed reference IDs."
            )
        errors, matched, within, correct = [], 0, 0, 0

        def timed(w):
            return w["start"] is not None and w["end"] is not None

        for a, b in pairs:
            expected, actual = reference[a], hypothesis[b]
            if timed(expected) and timed(actual):
                boundaries = [abs(expected[k] - actual[k]) for k in ("start", "end")]
                errors.extend(boundaries)
                matched += 1
                within += max(boundaries) <= 0.25
            if mapping is not None and expected["speaker"] is not None:
                correct += mapping.get(actual["speaker"]) == expected["speaker"]
        reference_speakers = sum(w["speaker"] is not None for w in reference)
        rows.append(
            {
                "id": clip["id"],
                "language": language,
                "duration_seconds": duration,
                "lexically_matched_words": len(pairs),
                "alignment_tie_decisions": ties,
                "timing": timing_summary(sum(timed(w) for w in reference), matched, errors, within),
                "speakers": speaker_summary(
                    reference_speakers, reference_speakers if mapping is not None else 0, correct
                ),
                "measurements": measured(prediction, duration),
                "contains_reviewed_edits": prediction.get("contains_reviewed_edits") is True,
            }
        )
        timing_errors.extend(errors)
        texts[clip["id"]] = prediction["text"]
    text_report = score(manifest, texts)
    for row, text in zip(rows, text_report["clips"], strict=True):
        row["text"] = {
            key: text[key]
            for key in (
                "word_errors",
                "reference_words",
                "character_errors",
                "reference_characters",
                "wer",
                "cer",
            )
        }
    by_language = {}
    for language in sorted({row["language"] for row in rows}):
        subset = [row for row in rows if row["language"] == language]
        totals = {
            key: sum(row["text"][key] for row in subset)
            for key in (
                "word_errors",
                "reference_words",
                "character_errors",
                "reference_characters",
            )
        }
        by_language[language] = {
            "wer": totals["word_errors"] / totals["reference_words"],
            "cer": totals["character_errors"] / totals["reference_characters"],
            "totals": totals,
            "clips": len(subset),
            "performance": performance_summary(subset),
        }
    return {
        "schema": "cbs.transcriber.review_evaluation.v1",
        "dataset": manifest["dataset"],
        "revision": manifest["revision"],
        "normalization": text_report["normalization"],
        "word_alignment": ALIGNMENT,
        "evidence": manifest.get("evidence", "Unspecified; no live quality claim"),
        "text": {key: text_report[key] for key in ("wer", "cer", "totals", "coverage")},
        "by_language": by_language,
        "performance": performance_summary(rows),
        "timing": timing_summary(
            sum(r["timing"]["reference_words"] for r in rows),
            sum(r["timing"]["matched_timed_words"] for r in rows),
            timing_errors,
            sum(r["timing"]["within_250ms_words"] for r in rows),
        ),
        "speakers": speaker_summary(
            sum(r["speakers"]["reference_words"] for r in rows),
            sum(r["speakers"]["scored_reference_words"] for r in rows),
            sum(r["speakers"]["correct_words"] for r in rows),
        ),
        "clips": rows,
    }
