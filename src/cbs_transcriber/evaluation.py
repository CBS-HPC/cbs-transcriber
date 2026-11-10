"""Deterministic reference-text scoring without an ASR or dataset dependency."""

import hashlib
import json
import unicodedata
from pathlib import Path

NORMALIZATION = "nfc-casefold-punctuation-to-space-whitespace-v1; CER includes spaces"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    text = "".join(" " if unicodedata.category(c).startswith("P") else c for c in text)
    return unicodedata.normalize("NFC", " ".join(text.split()))


def edit_distance(reference, hypothesis) -> int:
    """Levenshtein distance; one row of memory, adequate for these short clips."""
    previous = list(range(len(hypothesis) + 1))
    for i, expected in enumerate(reference, 1):
        current = [i]
        for j, actual in enumerate(hypothesis, 1):
            current.append(
                min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + (expected != actual))
            )
        previous = current
    return previous[-1]


def read_predictions(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate prediction ID: {key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def load_suite(directory: Path) -> dict:
    """Check the committed manifest and audio before running or scoring a suite."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    identifiers = set()
    if not manifest["clips"]:
        raise ValueError("Reference suite has no clips.")
    for clip in manifest["clips"]:
        identifier = clip["id"]
        if identifier in identifiers:
            raise ValueError(f"Duplicate reference ID: {identifier}")
        identifiers.add(identifier)
        if Path(clip["file"]).name != clip["file"]:
            raise ValueError("Fixture filename must be a basename.")
        if not isinstance(clip["text"], str) or not normalize(clip["text"]):
            raise ValueError(f"Empty reference: {identifier}")
        with (directory / clip["file"]).open("rb") as audio:
            actual = hashlib.file_digest(audio, "sha256").hexdigest()
        if actual != clip["sha256"]:
            raise ValueError(f"Fixture hash mismatch: {identifier}")
    return manifest


def score(manifest: dict, predictions: dict) -> dict:
    """Require complete ID coverage and aggregate error counts, not per-clip rates."""
    expected = {clip["id"] for clip in manifest["clips"]}
    if not isinstance(predictions, dict) or set(predictions) != expected:
        raise ValueError("Predictions must contain exactly one text for every reference clip ID.")
    results = []
    totals = dict.fromkeys(
        ["word_errors", "reference_words", "character_errors", "reference_characters"], 0
    )
    for clip in manifest["clips"]:
        hypothesis = predictions[clip["id"]]
        if not isinstance(hypothesis, str):
            raise ValueError(f"Prediction must be text: {clip['id']}")
        reference, predicted = normalize(clip["text"]), normalize(hypothesis)
        if not reference:
            raise ValueError(f"Empty reference: {clip['id']}")
        counts = {
            "word_errors": edit_distance(reference.split(), predicted.split()),
            "reference_words": len(reference.split()),
            "character_errors": edit_distance(reference, predicted),
            "reference_characters": len(reference),
        }
        results.append(
            {
                "id": clip["id"],
                "audio_sha256": clip.get("sha256"),
                "reference": clip["text"],
                "prediction": hypothesis,
                "normalized_reference": reference,
                "normalized_prediction": predicted,
                **counts,
                "wer": counts["word_errors"] / counts["reference_words"],
                "cer": counts["character_errors"] / counts["reference_characters"],
            }
        )
        for key in totals:
            totals[key] += counts[key]
    return {
        "dataset": manifest["dataset"],
        "revision": manifest["revision"],
        "normalization": NORMALIZATION,
        "coverage": {"expected": len(expected), "scored": len(results), "complete": True},
        "totals": totals,
        "wer": totals["word_errors"] / totals["reference_words"],
        "cer": totals["character_errors"] / totals["reference_characters"],
        "clips": results,
    }
