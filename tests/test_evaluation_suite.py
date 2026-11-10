"""Supplied output scoring retains pins and missing research coverage without inference."""

import copy
import hashlib
import json
import wave
from pathlib import Path

import pytest

from cbs_transcriber import evaluation_suite
from cbs_transcriber.evaluation_suite import (
    canonical_hash,
    compare,
    load_reference,
    main,
    read_json,
    validate_plan,
)

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = read_json(ROOT / "research/model-evaluation-candidates.json")
PLAN = read_json(ROOT / "research/danish-evaluation-plan.json")
PUBLIC = ROOT / "tests/fixtures/fleurs_da"


def run_for(plan, candidates, reference, *, candidate_id=None, cohort_id=None):
    candidate_id = candidate_id or plan["candidate_ids"][0]
    cohort_id = cohort_id or plan["cohorts"][0]["id"]
    candidate = next(c for c in candidates["candidates"] if c["id"] == candidate_id)
    return {
        "schema": "cbs.transcriber.supplied_model_predictions.v1",
        "id": "supplied-test-output",
        "candidate_id": candidate_id,
        "candidate_revision": candidate["revision"],
        "artifact": {"model": candidate["model"], "revision": candidate["revision"]},
        "runtime": {"engine": "transformers", "version": "unknown", "device": "unknown"},
        "cohort_id": cohort_id,
        "reference_manifest_sha256": canonical_hash(reference),
        "predictions": {clip["id"]: {"text": clip["text"]} for clip in reference["clips"]},
    }


def synthetic_reference(tmp_path, text="Hej team we agree.", language="da", *, mixed=False):
    directory = tmp_path / "reference"
    directory.mkdir()
    path = directory / "clip.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * 32000)
    manifest = {
        "dataset": "synthetic-reference",
        "revision": "synthetic-v1",
        "license": "test-only",
        "clips": [
            {
                "id": "PRIVATE_CLIP_LABEL",
                "file": "clip.wav",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "text": text,
                "language": language,
                "duration_seconds": 2,
                "recording_group": "PRIVATE_GROUP_LABEL",
                **({"code_switching": True} if mixed else {}),
            }
        ],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    plan = copy.deepcopy(PLAN)
    cohort = copy.deepcopy(plan["cohorts"][0])
    cohort.update(
        id="synthetic-switching" if mixed else "synthetic-da",
        language="da_en" if mixed else language,
        domain="interview",
        purpose="holdout",
        minimum_clips=2,
        minimum_recordings=2,
        minimum_seconds=10,
        reference={
            "dataset": manifest["dataset"],
            "revision": manifest["revision"],
            "license": manifest["license"],
            "manifest_sha256": canonical_hash(manifest),
        },
    )
    plan["cohorts"] = [cohort]
    return plan, directory, manifest


def test_committed_plan_pins_existing_public_reference_and_preserves_missing_cohorts():
    validate_plan(PLAN, CANDIDATES)
    reference = load_reference(PLAN["cohorts"][0], PUBLIC)
    report = compare(PLAN, CANDIDATES, {"fleurs-da-smoke": reference}, [])
    assert report["inference_performed"] is False
    assert report["coverage"]["scored_runs"] == 0
    assert report["coverage"]["missing_reference_cohorts"] == [
        "interview-da",
        "interview-en",
        "interview-da-en-switching",
    ]
    assert report["cohorts"][0]["reference_status"] == "loaded"
    assert report["cohorts"][0]["targets"]["met"] is True
    assert report["runs"] == []


def test_supplied_predictions_score_without_claiming_execution_or_legal_clearance():
    reference = load_reference(PLAN["cohorts"][0], PUBLIC)
    supplied = run_for(PLAN, CANDIDATES, reference)
    report = compare(PLAN, CANDIDATES, {"fleurs-da-smoke": reference}, [supplied])
    row = report["runs"][0]
    assert row["identity_verified"] is False
    assert row["evidence_kind"] == "operator_supplied_predictions"
    assert row["metrics"]["text"]["wer"] == 0
    assert row["metrics"]["timing"]["coverage"] is None
    assert row["metrics"]["speakers"]["word_accuracy"] is None
    assert row["metrics"]["performance"]["real_time_factor"] is None
    assert row["candidate"]["license"]["review_state"] == "pending_cbs_review"
    assert row["licence_clearance_verified"] is False
    assert row["supplied_payload_sha256"] == canonical_hash(supplied)
    assert "rank" not in row


def test_mixed_cohort_is_separate_and_incomplete_targets_remain_visible(tmp_path):
    plan, directory, _ = synthetic_reference(tmp_path, mixed=True)
    reference = load_reference(plan["cohorts"][0], directory)
    supplied = run_for(plan, CANDIDATES, reference)
    report = compare(plan, CANDIDATES, {plan["cohorts"][0]["id"]: reference}, [supplied])
    cohort = report["cohorts"][0]
    assert cohort["language"] == "da_en"
    assert cohort["targets"]["met"] is False
    assert cohort["reference_status"] == "loaded"
    encoded = json.dumps(report)
    assert "PRIVATE_" not in encoded
    assert "Hej team" not in encoded
    assert "language_boundary_accuracy" not in encoded
    assert report["runs"][0]["metrics"]["clips"][0]["id"] == "clip-001"


@pytest.mark.parametrize(
    "field", ["candidate_revision", "reference_manifest_sha256", "candidate_id", "extra"]
)
def test_mismatched_or_unknown_supplied_evidence_refuses(field):
    reference = load_reference(PLAN["cohorts"][0], PUBLIC)
    supplied = run_for(PLAN, CANDIDATES, reference)
    supplied[field] = "PRIVATE_WRONG_VALUE"
    with pytest.raises(ValueError):
        compare(PLAN, CANDIDATES, {"fleurs-da-smoke": reference}, [supplied])


def test_plan_rejects_changed_candidates_and_unpinned_references():
    altered = copy.deepcopy(CANDIDATES)
    altered["candidates"][0]["revision"] = "a" * 40
    with pytest.raises(ValueError, match="candidate"):
        validate_plan(PLAN, altered)
    reference = load_reference(PLAN["cohorts"][0], PUBLIC)
    changed = copy.deepcopy(reference)
    changed["clips"][0]["text"] += " wrong"
    with pytest.raises(ValueError, match="reference"):
        compare(PLAN, CANDIDATES, {"fleurs-da-smoke": changed}, [])


def test_reviewed_or_partial_predictions_cannot_be_reported_as_uncorrected_asr():
    reference = load_reference(PLAN["cohorts"][0], PUBLIC)
    supplied = run_for(PLAN, CANDIDATES, reference)
    first = next(iter(supplied["predictions"]))
    supplied["predictions"][first]["contains_reviewed_edits"] = True
    with pytest.raises(ValueError, match="(?i)review"):
        compare(PLAN, CANDIDATES, {"fleurs-da-smoke": reference}, [supplied])
    del supplied["predictions"][first]
    with pytest.raises(ValueError):
        compare(PLAN, CANDIDATES, {"fleurs-da-smoke": reference}, [supplied])


def test_json_and_cli_are_bounded_strict_and_never_overwrite(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"a":1,"a":2}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        read_json(bad)
    bad.write_text('{"a":NaN}', encoding="utf-8")
    with pytest.raises(ValueError):
        read_json(bad)
    output = tmp_path / "report.json"
    command = [
        "--plan",
        str(ROOT / "research/danish-evaluation-plan.json"),
        "--candidates",
        str(ROOT / "research/model-evaluation-candidates.json"),
        "--suite",
        "fleurs-da-smoke=" + str(PUBLIC),
        "--output",
        str(output),
    ]
    assert main(command) == 0
    original = output.read_bytes()
    assert main(command) == 1
    assert output.read_bytes() == original


def test_timing_speakers_and_measurements_use_existing_reviewed_metrics(tmp_path):
    plan, directory, reference = synthetic_reference(tmp_path)
    words = [
        {"word": token, "start": i * 0.4, "end": i * 0.4 + 0.3, "speaker": "R1" if i == 0 else "R2"}
        for i, token in enumerate(reference["clips"][0]["text"].split())
    ]
    reference["clips"][0]["reference_words"] = words
    plan["cohorts"][0]["reference"]["manifest_sha256"] = canonical_hash(reference)
    (directory / "manifest.json").write_text(json.dumps(reference), encoding="utf-8")
    reference = load_reference(plan["cohorts"][0], directory)
    supplied = run_for(plan, CANDIDATES, reference)
    prediction = supplied["predictions"][reference["clips"][0]["id"]]
    prediction["words"] = [
        {
            **word,
            "start": word["start"] + 0.1,
            "end": word["end"] + 0.1,
            "speaker": "A" if word["speaker"] == "R1" else "B",
        }
        for word in words
    ]
    prediction["speaker_map"] = {"A": "R1", "B": "R2"}
    prediction["measurements"] = {
        "elapsed_seconds": 1,
        "time_scope": "inference_only",
        "peak_rss_bytes": 4096,
        "memory_scope": "worker_peak_rss",
    }
    before = copy.deepcopy(supplied)
    metrics = compare(plan, CANDIDATES, {plan["cohorts"][0]["id"]: reference}, [supplied])["runs"][
        0
    ]["metrics"]
    assert supplied == before
    assert metrics["timing"]["coverage"] == 1
    assert metrics["timing"]["mean_boundary_error_seconds"] == pytest.approx(0.1)
    assert metrics["speakers"]["word_accuracy"] == 1
    assert metrics["performance"]["real_time_factor"] == 0.5


def test_converted_artifact_is_separately_pinned_and_does_not_inherit_licence_clearance():
    reference = load_reference(PLAN["cohorts"][0], PUBLIC)
    supplied = run_for(PLAN, CANDIDATES, reference)
    supplied["artifact"] = {"model": "conversion/example", "revision": "b" * 40}
    row = compare(PLAN, CANDIDATES, {"fleurs-da-smoke": reference}, [supplied])["runs"][0]
    assert row["artifact_matches_candidate"] is False
    assert row["identity_verified"] is False
    assert row["licence_clearance_verified"] is False
    assert any("Converted/different artifact" in gap for gap in row["gaps"])


def test_prediction_and_candidate_private_fields_cannot_be_serialized():
    reference = load_reference(PLAN["cohorts"][0], PUBLIC)
    supplied = run_for(PLAN, CANDIDATES, reference)
    supplied["runtime"]["api_key"] = "PRIVATE_KEY"
    with pytest.raises(ValueError, match="runtime"):
        compare(PLAN, CANDIDATES, {"fleurs-da-smoke": reference}, [supplied])
    candidates = copy.deepcopy(CANDIDATES)
    candidates["candidates"][0]["license"]["private_note"] = "PRIVATE_LICENCE_NOTE"
    plan = copy.deepcopy(PLAN)
    plan["candidate_manifest_sha256"] = canonical_hash(candidates)
    with pytest.raises(ValueError, match="licence"):
        validate_plan(plan, candidates)


@pytest.mark.parametrize("place", ["engine", "device", "review", "scope", "language"])
def test_invalid_scalar_metadata_refuses_cleanly(place):
    reference = load_reference(PLAN["cohorts"][0], PUBLIC)
    supplied = run_for(PLAN, CANDIDATES, reference)
    plan = copy.deepcopy(PLAN)
    prediction = next(iter(supplied["predictions"].values()))
    if place in {"engine", "device"}:
        supplied["runtime"][place] = []
    elif place == "review":
        prediction["contains_reviewed_edits"] = "false"
    elif place == "scope":
        prediction["measurements"] = {"elapsed_seconds": 1, "time_scope": []}
    else:
        plan["cohorts"][0]["language"] = []
    with pytest.raises(ValueError):
        compare(plan, CANDIDATES, {"fleurs-da-smoke": reference}, [supplied])


def test_bad_audio_geometry_and_unsafe_reference_paths_refuse(tmp_path):
    plan, directory, manifest = synthetic_reference(tmp_path)
    manifest["clips"][0]["file"] = "../PRIVATE_FILE.wav"
    plan["cohorts"][0]["reference"]["manifest_sha256"] = canonical_hash(manifest)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="basenames"):
        load_reference(plan["cohorts"][0], directory)
    manifest["clips"][0]["file"] = "clip.wav"
    manifest["clips"][0]["duration_seconds"] = 1
    plan["cohorts"][0]["reference"]["manifest_sha256"] = canonical_hash(manifest)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="duration"):
        load_reference(plan["cohorts"][0], directory)
    manifest["clips"][0]["duration_seconds"] = 2
    path = directory / "clip.wav"
    path.write_bytes(path.read_bytes()[:-50])
    manifest["clips"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    plan["cohorts"][0]["reference"]["manifest_sha256"] = canonical_hash(manifest)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        load_reference(plan["cohorts"][0], directory)


def test_duplicate_runs_unknown_cohort_and_missing_reference_refuse():
    reference = load_reference(PLAN["cohorts"][0], PUBLIC)
    supplied = run_for(PLAN, CANDIDATES, reference)
    for references, runs in [
        ({"fleurs-da-smoke": reference}, [supplied, supplied]),
        ({}, [supplied]),
        ({"unknown-cohort": reference}, []),
    ]:
        with pytest.raises(ValueError):
            compare(PLAN, CANDIDATES, references, runs)


def test_empty_references_do_not_gain_scores_or_recording_coverage(tmp_path):
    plan, directory, manifest = synthetic_reference(tmp_path)
    del manifest["clips"][0]["recording_group"]
    plan["cohorts"][0]["reference"]["manifest_sha256"] = canonical_hash(manifest)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    loaded = load_reference(plan["cohorts"][0], directory)
    report = compare(plan, CANDIDATES, {plan["cohorts"][0]["id"]: loaded}, [])
    assert report["cohorts"][0]["recording_groups"] is None
    assert report["cohorts"][0]["targets"]["met"] is False
    assert report["runs"] == []


@pytest.mark.parametrize("review_flag", [True, "false"], ids=["reviewed", "invalid-type"])
def test_segments_cannot_erase_top_level_review_evidence(review_flag):
    reference = load_reference(PLAN["cohorts"][0], PUBLIC)
    supplied = run_for(PLAN, CANDIDATES, reference)
    for clip in reference["clips"]:
        supplied["predictions"][clip["id"]] = {
            "segments": [{"text": clip["text"], "speaker": "UNKNOWN", "word_timing": False}],
            "contains_reviewed_edits": review_flag,
        }
    with pytest.raises(ValueError, match="(?i)review"):
        compare(PLAN, CANDIDATES, {"fleurs-da-smoke": reference}, [supplied])


def test_cumulative_run_budget_is_not_a_per_file_allowance(monkeypatch):
    reference = load_reference(PLAN["cohorts"][0], PUBLIC)
    supplied = run_for(PLAN, CANDIDATES, reference)
    for prediction in supplied["predictions"].values():
        prediction["text"] = "longword " * 1000
    second = copy.deepcopy(supplied)
    second["id"] = "second-run"
    monkeypatch.setattr(evaluation_suite, "MAX_JSON_BYTES", 60000)
    with pytest.raises(ValueError, match="Selected supplied runs"):
        compare(PLAN, CANDIDATES, {"fleurs-da-smoke": reference}, [supplied, second])


def test_source_url_cannot_carry_credentials_or_query_metadata():
    for source in (
        "https://user:PRIVATE_KEY@huggingface.co/model/LICENSE",
        "https://huggingface.co/model/LICENSE?key=PRIVATE_KEY",
        "https://huggingface.co/model/LICENSE#PRIVATE_KEY",
    ):
        candidates = copy.deepcopy(CANDIDATES)
        candidates["candidates"][0]["license"]["source"] = source
        plan = copy.deepcopy(PLAN)
        plan["candidate_manifest_sha256"] = canonical_hash(candidates)
        with pytest.raises(ValueError, match="primary-source"):
            validate_plan(plan, candidates)


def test_openai_compatible_transport_keeps_hf_identity_unverified():
    reference = load_reference(PLAN["cohorts"][0], PUBLIC)
    supplied = run_for(PLAN, CANDIDATES, reference)
    supplied["runtime"]["engine"] = "openai-api"
    with pytest.raises(ValueError, match="runtime"):
        compare(PLAN, CANDIDATES, {"fleurs-da-smoke": reference}, [supplied])
    supplied["runtime"]["engine"] = "openai-compatible"
    report = compare(PLAN, CANDIDATES, {"fleurs-da-smoke": reference}, [supplied])
    assert report["runs"][0]["identity_verified"] is False
