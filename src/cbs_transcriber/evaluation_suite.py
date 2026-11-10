"""Compare supplied model outputs across pinned research strata, entirely offline."""

import argparse
import copy
import hashlib
import json
import math
import re
import stat
import sys
import wave
from pathlib import Path
from urllib.parse import urlsplit

from .evaluation import load_suite, normalize
from .review_evaluation import annotated_words, from_saved_result, score_reviewed

PLAN_SCHEMA = "cbs.transcriber.model_evaluation_plan.v1"
RUN_SCHEMA = "cbs.transcriber.supplied_model_predictions.v1"
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_AUDIO_BYTES = 512 * 1024 * 1024
MAX_CLIPS = 100
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}")
SHA = re.compile(r"[a-f0-9]{64}")
REVISION = re.compile(r"[a-f0-9]{40}")
MODEL = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _fields(value, fields, message):
    _require(isinstance(value, dict) and set(value) == set(fields), message)


def _id(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def _hash(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def _text(value, maximum=200):
    return (
        isinstance(value, str) and 0 < len(value) <= maximum and not any(ord(c) < 32 for c in value)
    )


def _positive(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _choice(value, choices):
    return isinstance(value, str) and value in choices


def _canonical(value):
    digest, size = hashlib.sha256(), 0
    try:
        encoder = json.JSONEncoder(
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        for part in encoder.iterencode(value):
            data = part.encode("utf-8")
            size += len(data)
            _require(size <= MAX_JSON_BYTES, "Evaluation JSON exceeds the 32 MiB limit.")
            digest.update(data)
    except (TypeError, RecursionError, UnicodeError, OverflowError) as exc:
        raise ValueError("Evaluation JSON is invalid or too deeply nested.") from exc
    return digest.hexdigest(), size


def canonical_hash(value):
    """Hash the parsed JSON payload, not its original whitespace or byte serialization."""
    return _canonical(value)[0]


def read_json(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            _require(key not in value, "Duplicate JSON field.")
            value[key] = item
        return value

    path = Path(path)
    _require(
        not path.is_symlink() and stat.S_ISREG(path.stat().st_mode), "Choose a regular JSON file."
    )
    with path.open("rb") as stream:
        data = stream.read(MAX_JSON_BYTES + 1)
    _require(len(data) <= MAX_JSON_BYTES, "Evaluation JSON exceeds the 32 MiB limit.")
    try:
        value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique)
        canonical_hash(value)  # Reject nonfinite numbers even in fields not otherwise consumed.
        return value
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Evaluation JSON is invalid or too deeply nested.") from exc


def _candidates(document):
    _require(
        isinstance(document, dict)
        and document.get("schema") == "cbs.transcriber.model_evaluation_candidates.v1"
        and isinstance(document.get("candidates"), list)
        and 0 < len(document["candidates"]) <= 32,
        "Choose a bounded pinned candidate manifest.",
    )
    found = {}
    for row in document["candidates"]:
        _require(
            isinstance(row, dict)
            and _id(row.get("id"))
            and row["id"] not in found
            and isinstance(row.get("model"), str)
            and MODEL.fullmatch(row["model"])
            and isinstance(row.get("revision"), str)
            and REVISION.fullmatch(row["revision"]),
            "Candidate identities need unique IDs and exact model revisions.",
        )
        license = row.get("license")
        _require(
            isinstance(license, dict)
            and set(license) == {"declared_id", "source", "sha256", "review_state"}
            and _id(license.get("declared_id"))
            and _id(license.get("review_state"))
            and _text(license.get("source"), 1000)
            and (license.get("sha256") is None or _hash(license["sha256"])),
            "Candidate licence observations are invalid.",
        )
        source = urlsplit(license.get("source", ""))
        _require(
            source.scheme == "https"
            and source.hostname
            in {"huggingface.co", "github.com", "creativecommons.org", "www.apache.org"}
            and source.username is None
            and source.password is None
            and not source.query
            and not source.fragment,
            "Candidate licence observations need a public primary-source URL.",
        )
        found[row["id"]] = row
    return found


def validate_plan(plan, candidates):
    _fields(
        plan,
        ("schema", "id", "revision", "candidate_manifest_sha256", "candidate_ids", "cohorts"),
        "The evaluation plan has unsupported fields.",
    )
    _require(
        plan["schema"] == PLAN_SCHEMA and _id(plan["id"]) and _id(plan["revision"]),
        "The evaluation plan identity is invalid.",
    )
    _require(
        _hash(plan["candidate_manifest_sha256"])
        and canonical_hash(candidates) == plan["candidate_manifest_sha256"],
        "The candidate manifest differs from this evaluation plan.",
    )
    found = _candidates(candidates)
    selected = plan["candidate_ids"]
    _require(
        isinstance(selected, list)
        and 0 < len(selected) <= 16
        and all(_id(v) and v in found for v in selected)
        and len(set(selected)) == len(selected),
        "Choose unique pinned candidate IDs.",
    )
    cohorts = plan["cohorts"]
    _require(isinstance(cohorts, list) and 0 < len(cohorts) <= 8, "Choose 1–8 evaluation cohorts.")
    seen = set()
    for row in cohorts:
        _fields(
            row,
            (
                "id",
                "language",
                "domain",
                "purpose",
                "minimum_clips",
                "minimum_recordings",
                "minimum_seconds",
                "reference",
            ),
            "The cohort has unsupported fields.",
        )
        _require(
            _id(row["id"])
            and row["id"] not in seen
            and _choice(row["language"], {"da", "en", "da_en"})
            and _choice(row["domain"], {"read_speech", "interview"})
            and _choice(row["purpose"], {"smoke", "holdout"}),
            "The cohort identity or stratum is invalid.",
        )
        seen.add(row["id"])
        _require(
            type(row["minimum_clips"]) is int
            and 1 <= row["minimum_clips"] <= MAX_CLIPS
            and (
                row["minimum_recordings"] is None
                or type(row["minimum_recordings"]) is int
                and 1 <= row["minimum_recordings"] <= MAX_CLIPS
            )
            and _positive(row["minimum_seconds"])
            and row["minimum_seconds"] <= 120000,
            "Cohort targets must be bounded positive counts/duration.",
        )
        if row["reference"] is not None:
            ref = row["reference"]
            _fields(
                ref,
                ("dataset", "revision", "license", "manifest_sha256"),
                "The reference identity has unsupported fields.",
            )
            _require(
                all(_text(ref[k]) for k in ("dataset", "revision", "license"))
                and _hash(ref["manifest_sha256"]),
                "Pin the reference manifest and its rights observation.",
            )
    return plan


def _reference(cohort, manifest):
    expected = cohort["reference"]
    _require(
        expected is not None and isinstance(manifest, dict), "This reference cohort is not pinned."
    )
    _require(
        canonical_hash(manifest) == expected["manifest_sha256"]
        and all(manifest.get(k) == expected[k] for k in ("dataset", "revision", "license")),
        "The reference manifest differs from this evaluation plan.",
    )
    clips = manifest.get("clips")
    _require(
        isinstance(clips, list) and 0 < len(clips) <= MAX_CLIPS,
        "Reference clips must be a bounded nonempty list.",
    )
    seen = set()
    for clip in clips:
        _require(
            isinstance(clip, dict)
            and _id(clip.get("id"))
            and clip["id"] not in seen
            and _hash(clip.get("sha256")),
            "Reference clips need unique IDs and audio hashes.",
        )
        seen.add(clip["id"])
        language = clip.get("language", manifest.get("language", ""))
        _require(
            isinstance(language, str)
            and language.replace("-", "_").split("_")[0]
            in ({"da", "en"} if cohort["language"] == "da_en" else {cohort["language"]}),
            "Reference language labels differ from the declared cohort.",
        )
        _require(
            cohort["language"] != "da_en" or clip.get("code_switching") is True,
            "Mixed-language references need an explicit code-switching annotation.",
        )
        _require(
            _positive(clip.get("duration_seconds")) and clip["duration_seconds"] <= 1200,
            "Use reference clips of at most 20 minutes.",
        )
        _require(
            isinstance(clip.get("text"), str) and bool(normalize(clip["text"])),
            "Reference clips need complete nonblank text.",
        )
        annotations = annotated_words(
            clip["text"], clip.get("reference_words"), clip["duration_seconds"]
        )
        starts = [word["start"] for word in annotations if word["start"] is not None]
        _require(starts == sorted(starts), "Reference annotations must follow transcript order.")
        _require(
            clip.get("recording_group") is None or _id(clip["recording_group"]),
            "Recording groups need bounded opaque IDs.",
        )
    return manifest


def load_reference(cohort, directory):
    """Verify selected local WAVs using the existing hash loader and explicit clip geometry."""
    directory = Path(directory)
    declared = read_json(directory / "manifest.json")
    _reference(cohort, declared)
    for clip in declared["clips"]:
        name = clip.get("file")
        _require(
            isinstance(name, str)
            and 0 < len(name) <= 200
            and not any(c in name for c in "\\/:")
            and Path(name).suffix.lower() == ".wav",
            "Reference WAV names must be basenames.",
        )
        path = directory / name
        _require(
            not path.is_symlink()
            and stat.S_ISREG(path.stat().st_mode)
            and path.stat().st_size <= MAX_AUDIO_BYTES,
            "Reference audio must be a regular WAV file.",
        )
        with wave.open(str(path), "rb") as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            _require(
                0 < rate <= 384000
                and frames > 0
                and 1 <= audio.getnchannels() <= 8
                and 1 <= audio.getsampwidth() <= 4
                and abs(frames / rate - clip["duration_seconds"]) <= 0.5 / rate,
                "Reference WAV duration differs from its manifest.",
            )
            sample_bytes = audio.getnchannels() * audio.getsampwidth()
            received = 0
            while block := audio.readframes(min(rate * 10, 65536)):
                received += len(block)
            _require(received == frames * sample_bytes, "Reference WAV is incomplete.")
    actual = load_suite(directory)
    _require(
        canonical_hash(actual) == canonical_hash(declared),
        "The reference manifest changed while loading.",
    )
    return actual


def _prediction(value):
    _require(
        isinstance(value, dict)
        and not set(value)
        - {"text", "words", "segments", "speaker_map", "measurements", "contains_reviewed_edits"},
        "The supplied prediction has unsupported fields.",
    )
    _require(
        ("text" in value) != ("segments" in value)
        and ("segments" not in value or "words" not in value),
        "Choose one complete prediction representation.",
    )
    _require(
        "contains_reviewed_edits" not in value or type(value["contains_reviewed_edits"]) is bool,
        "Review metadata must be boolean when supplied.",
    )
    explicitly_reviewed = value.get("contains_reviewed_edits") is True
    value = copy.deepcopy(value)
    value = from_saved_result(value) if "segments" in value else value
    _require(
        not explicitly_reviewed and value.get("contains_reviewed_edits") is not True,
        "Reviewed wording cannot stand in for uncorrected ASR output.",
    )
    measured = value.get("measurements")
    if measured is not None:
        _require(
            isinstance(measured, dict)
            and not set(measured)
            - {"elapsed_seconds", "time_scope", "peak_rss_bytes", "memory_scope"},
            "Supplied measurements have unsupported fields.",
        )
        _require(
            (
                measured.get("time_scope") is None
                or _choice(
                    measured["time_scope"],
                    {"inference_only", "full_pipeline", "queue_to_completion"},
                )
            )
            and (
                measured.get("memory_scope") is None
                or _choice(measured["memory_scope"], {"worker_peak_rss", "process_tree_peak_rss"})
            ),
            "Declare a supported measurement scope.",
        )
        value["measurements"] = {
            **measured,
            "source": "operator-supplied; not measured by this tool",
        }
    return value


def compare(plan, candidates, references, runs):
    validate_plan(plan, candidates)
    selected = _candidates(candidates)
    by_cohort = {row["id"]: row for row in plan["cohorts"]}
    _require(
        isinstance(references, dict) and set(references) <= set(by_cohort),
        "Choose only declared reference cohorts.",
    )
    reference_bytes = 0
    for key, value in references.items():
        reference_bytes += _canonical(value)[1]
        _require(reference_bytes <= MAX_JSON_BYTES, "Selected reference metadata exceeds 32 MiB.")
        _reference(by_cohort[key], value)
    _require(isinstance(runs, list) and len(runs) <= 32, "Compare at most 32 supplied runs.")
    output, seen, covered, run_bytes = [], set(), set(), 0
    for run in runs:
        _fields(
            run,
            (
                "schema",
                "id",
                "candidate_id",
                "candidate_revision",
                "artifact",
                "runtime",
                "cohort_id",
                "reference_manifest_sha256",
                "predictions",
            ),
            "The supplied run has unsupported fields.",
        )
        digest, size = _canonical(run)
        run_bytes += size
        _require(run_bytes <= MAX_JSON_BYTES, "Selected supplied runs exceed the 32 MiB limit.")
        _require(
            run["schema"] == RUN_SCHEMA and _id(run["id"]) and run["id"] not in seen,
            "Supplied runs need unique bounded identities.",
        )
        seen.add(run["id"])
        identifier, cohort_id = run["candidate_id"], run["cohort_id"]
        _require(
            isinstance(identifier, str)
            and identifier in plan["candidate_ids"]
            and isinstance(cohort_id, str)
            and cohort_id in references,
            "The supplied candidate/reference pair is unavailable.",
        )
        candidate = selected[identifier]
        _require(
            run["candidate_revision"] == candidate["revision"]
            and run["reference_manifest_sha256"] == canonical_hash(references[cohort_id]),
            "The supplied candidate/reference revision differs from its pin.",
        )
        artifact, runtime = run["artifact"], run["runtime"]
        _fields(artifact, ("model", "revision"), "The supplied artifact identity is invalid.")
        _require(
            isinstance(artifact["model"], str)
            and MODEL.fullmatch(artifact["model"])
            and isinstance(artifact["revision"], str)
            and REVISION.fullmatch(artifact["revision"]),
            "The supplied artifact needs an exact repository revision.",
        )
        _fields(
            runtime, ("engine", "version", "device"), "The supplied runtime identity is invalid."
        )
        _require(
            _choice(
                runtime["engine"],
                {
                    "transformers",
                    "faster-whisper",
                    "nemo",
                    "custom",
                    "openai-compatible",
                    "whisperx",
                },
            )
            and _id(runtime["version"])
            and _choice(runtime["device"], {"cpu", "cuda", "mps", "unknown"}),
            "The supplied runtime identity is invalid.",
        )
        predictions = run["predictions"]
        _require(
            isinstance(predictions, dict)
            and len(predictions) <= MAX_CLIPS
            and set(predictions) == {clip["id"] for clip in references[cohort_id]["clips"]},
            "Supplied predictions must cover their reference cohort.",
        )
        metrics = score_reviewed(
            references[cohort_id], {key: _prediction(value) for key, value in predictions.items()}
        )
        safe_metrics = {
            key: metrics[key]
            for key in (
                "text",
                "timing",
                "speakers",
                "performance",
                "normalization",
                "word_alignment",
            )
        }
        safe_metrics["clips"] = [
            {**row, "id": f"clip-{i:03}"} for i, row in enumerate(metrics["clips"], 1)
        ]
        output.append(
            {
                "id": f"run-{len(output) + 1:03}",
                "cohort_id": cohort_id,
                "candidate": {
                    **{key: candidate[key] for key in ("id", "model", "revision")},
                    "license": {
                        key: candidate["license"][key]
                        for key in ("declared_id", "source", "sha256", "review_state")
                    },
                },
                "artifact": dict(artifact),
                "runtime_observation": dict(runtime),
                "artifact_matches_candidate": artifact
                == {"model": candidate["model"], "revision": candidate["revision"]},
                "evidence_kind": "operator_supplied_predictions",
                "identity_verified": False,
                "licence_clearance_verified": False,
                "reviewed_edits_check": "supplied_metadata_only",
                "supplied_payload_sha256": digest,
                "reference_manifest_sha256": run["reference_manifest_sha256"],
                "metrics": safe_metrics,
                "gaps": [
                    "Model execution, artifact ancestry and runtime are operator-supplied, "
                    "not independently verified.",
                    "Candidate licence observations do not establish artifact-specific "
                    "CBS clearance.",
                    "GPU memory, cost and language-boundary accuracy are not collected "
                    "by this scorer.",
                ]
                + (
                    [
                        "Converted/different artifact: governing licence, conversion and "
                        "base association remain unverified."
                    ]
                    if artifact != {"model": candidate["model"], "revision": candidate["revision"]}
                    else []
                ),
            }
        )
        covered.add((identifier, cohort_id))
    cohorts = []
    for cohort in plan["cohorts"]:
        key = cohort["id"]
        manifest = references.get(key)
        clips = manifest["clips"] if manifest is not None else []
        groups = {
            clip["recording_group"] for clip in clips if clip.get("recording_group") is not None
        }
        duration = sum(clip["duration_seconds"] for clip in clips)
        targets_met = (
            manifest is not None
            and len(clips) >= cohort["minimum_clips"]
            and duration >= cohort["minimum_seconds"]
            and (
                cohort["minimum_recordings"] is None or len(groups) >= cohort["minimum_recordings"]
            )
        )
        cohorts.append(
            {
                "id": key,
                "language": cohort["language"],
                "domain": cohort["domain"],
                "purpose": cohort["purpose"],
                "reference_status": "loaded"
                if manifest is not None
                else "not_pinned"
                if cohort["reference"] is None
                else "not_loaded",
                "reference_manifest_sha256": canonical_hash(manifest)
                if manifest is not None
                else None,
                "clips": len(clips),
                "duration_seconds": duration if manifest is not None else None,
                "recording_groups": len(groups) if groups else None,
                "targets": {
                    "clips": cohort["minimum_clips"],
                    "recordings": cohort["minimum_recordings"],
                    "seconds": cohort["minimum_seconds"],
                    "met": bool(targets_met),
                },
                "missing_candidates": [
                    identifier
                    for identifier in plan["candidate_ids"]
                    if (identifier, key) not in covered
                ],
            }
        )
    return {
        "schema": "cbs.transcriber.model_evaluation_comparison.v1",
        "plan_id": plan["id"],
        "plan_sha256": canonical_hash(plan),
        "candidate_manifest_sha256": canonical_hash(candidates),
        "inference_performed": False,
        "coverage": {
            "scored_runs": len(output),
            "candidate_cohorts_scored": len(covered),
            "candidate_cohorts_expected": len(plan["candidate_ids"]) * len(cohorts),
            "missing_reference_cohorts": [
                row["id"] for row in cohorts if row["reference_status"] != "loaded"
            ],
        },
        "cohorts": cohorts,
        "runs": output,
        "limitations": [
            "Scores describe supplied outputs; no automatic quality ranking or deployment "
            "approval is produced.",
            "Cohort targets are planning targets, not statistical power or journal-compliance "
            "certification.",
            "Code-switching is a declared stratum; no language-identification correctness "
            "is inferred.",
            "Holdout purpose is an operator declaration, not proof of exclusion from training "
            "or checkpoint selection.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--suite", action="append", default=[], metavar="COHORT=FOLDER")
    parser.add_argument("--run", action="append", default=[], type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New report; never overwritten")
    args = parser.parse_args(argv)
    try:
        plan, candidates = read_json(args.plan), read_json(args.candidates)
        validate_plan(plan, candidates)
        cohorts, references = {row["id"]: row for row in plan["cohorts"]}, {}
        for item in args.suite:
            key, separator, directory = item.partition("=")
            _require(
                separator and key in cohorts and key not in references,
                "Use unique declared COHORT=FOLDER references.",
            )
            references[key] = load_reference(cohorts[key], Path(directory))
        runs, loaded_bytes = [], 0
        for path in args.run:
            supplied = read_json(path)
            loaded_bytes += _canonical(supplied)[1]
            _require(loaded_bytes <= MAX_JSON_BYTES, "Selected supplied runs exceed 32 MiB.")
            runs.append(supplied)
        report = compare(plan, candidates, references, runs)
        payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    except (ValueError, OSError, KeyError, TypeError, wave.Error, EOFError, OverflowError) as exc:
        print(
            "Evaluation failed. "
            + (
                str(exc)
                if isinstance(exc, ValueError)
                else "Check input files and choose a new output path."
            )
        )
        return 1
    print(f"Compared {len(report['runs'])} supplied runs; no inference was performed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
