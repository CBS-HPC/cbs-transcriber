import hashlib
import json
import runpy
from copy import deepcopy
from pathlib import Path

import pytest

from cbs_transcriber.evaluation import load_suite, score
from cbs_transcriber.review_evaluation import (
    annotated_words,
    from_saved_result,
    lexical_pairs,
    number,
    score_reviewed,
)
from cbs_transcriber.transcripts import edit_segment, normalize_segments

RUNNER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/evaluate_reviewed.py"))


def suite():
    def words(text):
        return [
            {"word": w, "start": i * 0.5, "end": (i + 1) * 0.5, "speaker": "R1" if i == 0 else "R2"}
            for i, w in enumerate(text.split())
        ]

    return {
        "dataset": "synthetic bilingual review",
        "revision": "test-v1",
        "evidence": "Synthetic metric fixture, not model-quality evidence",
        "clips": [
            {
                "id": "da",
                "language": "da",
                "duration_seconds": 3,
                "file": "da.wav",
                "text": "Hej København igen.",
                "reference_words": words("Hej København igen."),
            },
            {
                "id": "en",
                "language": "en",
                "duration_seconds": 3,
                "file": "en.wav",
                "text": "We agree.",
                "reference_words": words("We agree."),
            },
        ],
    }


def predictions():
    def entry(text):
        return {
            "text": text,
            "speaker_map": {"A": "R1", "B": "R2"},
            "words": [
                {
                    "word": w,
                    "start": i * 0.5 + 0.1,
                    "end": (i + 1) * 0.5 + 0.1,
                    "speaker": "A" if i == 0 else "B",
                }
                for i, w in enumerate(text.split())
            ],
        }

    return {
        "da": {
            **entry("Hej København."),
            "measurements": {
                "source": "synthetic measurement",
                "elapsed_seconds": 1.5,
                "time_scope": "synthetic inference only",
                "peak_rss_bytes": 1000,
                "memory_scope": "synthetic process",
            },
        },
        "en": entry("We disagree."),
    }


def test_v35_corpus_language_timing_speaker_and_reported_measurement_coverage():
    report = score_reviewed(suite(), predictions())
    assert report["text"]["wer"] == 2 / 5
    assert report["by_language"]["da"]["wer"] == 1 / 3
    assert report["by_language"]["en"]["wer"] == 1 / 2
    assert report["timing"]["coverage"] == 3 / 5
    assert report["timing"]["mean_boundary_error_seconds"] == pytest.approx(0.1)
    assert report["timing"]["within_250ms_fraction"] == 1
    assert report["speakers"]["word_accuracy"] == 3 / 5  # Substitution/deletion count against it.
    assert report["performance"]["measured_time_clips"] == 1
    assert report["performance"]["real_time_factor"] == 0.5
    assert report["performance"]["maximum_reported_peak_rss_bytes"] == 1000
    assert report["clips"][1]["measurements"]["peak_rss_bytes"] is None
    assert "Hej" not in json.dumps(report)  # Numeric reports omit transcript bodies.


def test_v35_missing_timing_mapping_and_measurement_remain_missing():
    provided = {key: {"text": value["text"]} for key, value in predictions().items()}
    report = score_reviewed(suite(), provided)
    assert (
        report["timing"]["coverage"] == 0
        and report["timing"]["mean_boundary_error_seconds"] is None
    )
    assert report["speakers"]["word_accuracy"] is None
    assert report["speakers"]["coverage"] == 0
    assert report["performance"]["real_time_factor"] is None
    plain = suite()
    for clip in plain["clips"]:
        clip.pop("reference_words")
    report = score_reviewed(plain, provided)
    assert report["timing"]["coverage"] is None and report["speakers"]["coverage"] is None


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"source": "x", "elapsed_seconds": 1},
        {"source": "x", "peak_rss_bytes": 1},
        {"source": "x", "peak_rss_bytes": True, "memory_scope": "process"},
        {"source": "x", "elapsed_seconds": 0, "time_scope": "inference"},
    ],
)
def test_v35_measurements_require_valid_units_provenance_and_scopes(values):
    data = predictions()
    data["da"]["measurements"] = values
    with pytest.raises(ValueError):
        score_reviewed(suite(), data)


def test_v35_mixed_measurement_scopes_are_not_silently_combined():
    data = predictions()
    data["en"]["measurements"] = {
        "source": "synthetic",
        "elapsed_seconds": 2,
        "time_scope": "end to end",
        "peak_rss_bytes": 2000,
        "memory_scope": "whole application",
    }
    report = score_reviewed(suite(), data)
    assert report["performance"]["real_time_factor"] is None
    assert report["performance"]["maximum_reported_peak_rss_bytes"] is None


def test_v35_speaker_ids_require_explicit_mapping_and_scored_coverage():
    data = predictions()
    data["en"].pop("speaker_map")
    report = score_reviewed(suite(), data)
    assert report["speakers"]["coverage"] == 3 / 5
    assert report["speakers"]["word_accuracy"] == 2 / 3
    data["da"]["speaker_map"]["A"] = "not-reviewed"
    with pytest.raises(ValueError, match="mappings"):
        score_reviewed(suite(), data)


def test_v35_repeated_word_ties_are_disclosed_without_using_timing_to_choose():
    reference = annotated_words(
        "ja ja", [{"word": "ja", "start": 0, "end": 1}, {"word": "ja", "start": 1, "end": 2}], 3
    )
    hypothesis = annotated_words("ja", [{"word": "ja", "start": 0, "end": 1}], 3)
    pairs, ties = lexical_pairs(reference, hypothesis)
    assert pairs == [(1, 0)] and ties > 0  # Fixed traceback, despite first interval being closer.


@pytest.mark.parametrize(
    "change", ["word", "outside", "reference-order", "coverage", "language", "bound", "measurement"]
)
def test_v35_malformed_annotations_and_incomplete_predictions_are_rejected(change):
    manifest, data = suite(), predictions()
    if change == "word":
        data["da"]["words"][0]["word"] = "different"
    elif change == "outside":
        data["da"]["words"][0]["end"] = 10
    elif change == "reference-order":
        manifest["clips"][0]["reference_words"][0].update(start=2, end=2.5)
    elif change == "coverage":
        data.pop("en")
    elif change == "language":
        manifest["clips"][0]["language"] = None
    elif change == "bound":
        data["da"] = {"text": "x " * 2001}
    else:
        data["da"]["measurements"]["elapsed_seconds"] = float("nan")
    with pytest.raises(ValueError):
        score_reviewed(manifest, data)


def test_v35_empty_predictions_unicode_and_saved_free_text_never_invent_word_times():
    manifest = suite()
    report = score_reviewed(manifest, {key: {"text": ""} for key in predictions()})
    assert report["text"]["wer"] == 1 and report["timing"]["coverage"] == 0
    segments = normalize_segments(
        [
            {
                "text": "Hej København.",
                "start": 0,
                "end": 3,
                "words": [
                    {"word": "Hej", "start": 0, "end": 1},
                    {"word": "København.", "start": 1, "end": 3},
                ],
            }
        ]
    )
    prediction = from_saved_result({"segments": segments})
    assert prediction["words"][1]["start"] == 1
    rewritten = edit_segment(segments[0], "free", text="Hej Københavns forskere.")
    prediction = from_saved_result({"segments": [rewritten]})
    assert prediction["contains_reviewed_edits"] and all(
        w["start"] is None for w in prediction["words"]
    )
    assert number(0) == 0
    with pytest.raises(ValueError):
        number(10**1000)


@pytest.mark.parametrize("current_partial", [False, True], ids=["unedited", "structural-current"])
def test_valid_partial_saved_maps_keep_observed_timing_coverage(current_partial):
    segments = normalize_segments(
        [
            {
                "text": "one two",
                "start": 0,
                "end": 2,
                "words": [{"word": "one", "start": 0, "end": 1}],
            }
        ]
    )
    assert not segments[0]["word_timing"]
    if current_partial:
        segments[0].update(edited=True, words_current=True)
    prediction = {"segments": segments}
    before = deepcopy(prediction)
    manifest = {
        "dataset": "synthetic partial map",
        "revision": "test",
        "clips": [
            {
                "id": "clip",
                "language": "en",
                "duration_seconds": 2,
                "text": "one two",
                "reference_words": [
                    {"word": "one", "start": 0, "end": 1},
                    {"word": "two", "start": 1, "end": 2},
                ],
            }
        ],
    }
    report = score_reviewed(manifest, {"clip": prediction})
    assert report["text"]["wer"] == report["text"]["cer"] == 0
    assert report["timing"]["reference_words"] == 2
    assert report["timing"]["matched_timed_words"] == 1
    assert report["timing"]["coverage"] == 0.5
    assert report["timing"]["mean_boundary_error_seconds"] == 0
    assert prediction == before
    # A free rewrite cannot recover a stale map, even when its first word still matches.
    segments[0]["edited"] = True
    segments[0].pop("words_current", None)
    assert score_reviewed(manifest, {"clip": prediction})["timing"]["coverage"] == 0


@pytest.mark.parametrize("offsets", [(False, True), (0.0, 1.0)])
def test_saved_timing_requires_literal_integer_character_offsets(offsets):
    result = from_saved_result(
        {
            "segments": [
                {
                    "text": "i",
                    "word_timing": True,
                    "words": [
                        {
                            "word": "i",
                            "char_start": offsets[0],
                            "char_end": offsets[1],
                            "start": 0,
                            "end": 1,
                        }
                    ],
                }
            ]
        }
    )
    assert result["words"][0]["start"] is result["words"][0]["end"] is None


@pytest.mark.parametrize(
    "bad_interval,normalized",
    [
        ((0, 0.5), True),
        ((8, 9), True),
        ((2, 2), True),
        ((2, None), True),
        ((True, 3), False),
        (("2", 3), False),
        ((2, float("nan")), False),
        ((2, float("inf")), False),
        ((2, 10**1000), False),
    ],
    ids=[
        "before-passage",
        "after-passage",
        "zero",
        "missing-end",
        "boolean",
        "string",
        "nan",
        "infinite",
        "huge-integer",
    ],
)
def test_invalid_partial_saved_intervals_are_untimed_without_losing_valid_words(
    bad_interval, normalized
):
    middle_start, middle_end = bad_interval if normalized else (2, 2)
    segments = normalize_segments(
        [
            {
                "text": "one two three",
                "start": 1,
                "end": 4,
                "speaker": "A",
                "words": [
                    {"word": "one", "start": 1, "end": 2},
                    {"word": "two", "start": middle_start, "end": middle_end},
                    {"word": "three", "start": 3, "end": 4},
                ],
            }
        ]
    )
    assert not segments[0]["word_timing"]
    if not normalized:  # Scalar corruption is not accepted by the transcript normalizer.
        segments[0]["words"][1].update(start=bad_interval[0], end=bad_interval[1])
    prediction = {"segments": segments, "speaker_map": {"A": "R1"}}
    before = repr(prediction)
    manifest = {
        "dataset": "synthetic invalid partial",
        "revision": "test",
        "clips": [
            {
                "id": "clip",
                "language": "en",
                "duration_seconds": 10,
                "text": "one two three",
                "reference_words": [
                    {"word": word, "start": index + 1, "end": index + 2, "speaker": "R1"}
                    for index, word in enumerate(("one", "two", "three"))
                ],
            }
        ],
    }
    report = score_reviewed(manifest, {"clip": prediction})
    converted = from_saved_result(prediction)
    assert [(word["start"], word["end"]) for word in converted["words"]] == [
        (1, 2),
        (None, None),
        (3, 4),
    ]
    assert [word["word"] for word in converted["words"]] == ["one", "two", "three"]
    assert all(word["speaker"] == "A" for word in converted["words"])
    assert report["text"]["wer"] == 0
    assert report["timing"]["coverage"] == 2 / 3
    assert report["timing"]["mean_boundary_error_seconds"] == 0
    assert report["speakers"]["word_accuracy"] == 1
    assert repr(prediction) == before


@pytest.mark.parametrize(
    "first,bad", [((2, 2.5), (1.5, 3)), ((1, 3), (2, 2.5))], ids=["start", "end"]
)
def test_partial_saved_order_uses_only_previous_valid_interval(first, bad):
    segments = normalize_segments(
        [
            {
                "text": "one two three",
                "start": 1,
                "end": 4,
                "words": [
                    {"word": "one", "start": first[0], "end": first[1]},
                    {"word": "two", "start": bad[0], "end": bad[1]},
                    {"word": "three", "start": 3, "end": 4},
                ],
            }
        ]
    )
    assert not segments[0]["word_timing"]
    assert [
        (word["start"], word["end"]) for word in from_saved_result({"segments": segments})["words"]
    ] == [first, (None, None), (3, 4)]


@pytest.mark.parametrize("bounds", [(None, None), (None, 4), (1, None)])
def test_partial_saved_map_keeps_valid_words_with_missing_passage_bounds(bounds):
    segments = normalize_segments(
        [
            {
                "text": "one two three",
                "start": bounds[0],
                "end": bounds[1],
                "words": [
                    {"word": "one", "start": 1, "end": 2},
                    {"word": "two", "start": 2, "end": 2},
                    {"word": "three", "start": 3, "end": 4},
                ],
            }
        ]
    )
    assert [
        (word["start"], word["end"]) for word in from_saved_result({"segments": segments})["words"]
    ] == [(1, 2), (None, None), (3, 4)]


def test_v35_hashes_stream_and_offline_cli_compares_without_overwriting(
    tmp_path, wav_bytes, monkeypatch
):
    manifest = suite()
    for clip in manifest["clips"]:
        (tmp_path / clip["file"]).write_bytes(wav_bytes)
        clip["sha256"] = hashlib.sha256(wav_bytes).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    first = tmp_path / "predictions.json"
    first.write_text(json.dumps(predictions()), encoding="utf-8")
    monkeypatch.setattr(Path, "read_bytes", lambda *a: pytest.fail("whole media read"))
    assert load_suite(tmp_path)["clips"][0]["sha256"] == manifest["clips"][0]["sha256"]
    output = tmp_path / "output.json"
    args = [
        "--suite",
        str(tmp_path),
        "--prediction",
        "Method A=" + str(first),
        "--prediction",
        "Method B=" + str(first),
        "--output",
        str(output),
    ]
    assert RUNNER["main"](args) == 0
    content = output.read_text(encoding="utf-8")
    assert len(json.loads(content)["methods"]) == 2
    assert RUNNER["main"](args) == 1 and output.read_text(encoding="utf-8") == content
    old = score(manifest, {key: value["text"] for key, value in predictions().items()})
    assert json.loads(content)["methods"]["Method A"]["text"]["cer"] == old["cer"]
    (tmp_path / "da.wav").write_bytes(b"damaged")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_suite(tmp_path)
