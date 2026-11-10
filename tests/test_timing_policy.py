"""Valid alignment estimates stay playable under an explicitly frozen timing policy."""

import copy

import pytest

from cbs_transcriber import enhancement, runtime_batch_remote
from cbs_transcriber.transcripts import normalize_segments, pop_hints


def source_and_output():
    source = {
        "segments": normalize_segments(
            [{"text": "Jeg er her.", "start": 0, "end": 2, "speaker": "A"}]
        ),
        "speakers": {"A": "Reviewed person"},
    }
    output = {
        "passages": [
            {
                "id": "0",
                "words": [
                    {"word": "Jeg", "start": 0.1, "end": 0.3, "score": 0.005},
                    {"word": "er", "start": 0.4, "end": 0.6, "score": 0.8},
                    {"word": "her.", "start": 0.7, "end": 1.0, "score": 0.1},
                ],
            }
        ]
    }
    return source, output


def test_explicit_policy_retains_valid_low_score_timing_and_uncertainty_hints():
    source, output = source_and_output()
    before = copy.deepcopy((source, output))
    options = enhancement.EnhancementOptions(timing_policy="retain-estimates")
    rows, _ = enhancement.merge_output(source, output, None, options, 2, "scope")
    assert rows[0]["word_timing"] is True
    assert [(word["start"], word["end"]) for word in rows[0]["words"]] == [
        (0.1, 0.3),
        (0.4, 0.6),
        (0.7, 1.0),
    ]
    hints = pop_hints(rows)
    assert hints["0"]["word_kind"] == "alignment"
    assert hints["0"]["words"] == [[0, 3, 0.005], [4, 6, 0.8], [7, 11, 0.1]]
    assert hints and (source, output) == before


def test_missing_policy_keeps_legacy_filter_and_exact_serialized_shape():
    source, output = source_and_output()
    options = enhancement.EnhancementOptions.model_validate({"min_score": 0.3})
    assert options.timing_policy == "legacy-score-filter"
    assert "timing_policy" not in options.model_dump()
    assert "timing_policy" not in options.model_copy(update={"device": "cuda"}).model_dump()
    rows, _ = enhancement.merge_output(source, output, None, options, 2, "scope")
    assert rows[0]["word_timing"] is False
    assert [word["start"] for word in rows[0]["words"]] == [None, 0.4, None]
    explicit = options.model_copy(update={"timing_policy": "retain-estimates"})
    assert explicit.model_dump()["timing_policy"] == "retain-estimates"


@pytest.mark.parametrize("damage", ["outside_recording", "outside_passage", "out_of_order"])
def test_retained_estimates_do_not_relax_interval_validation(damage):
    source, output = source_and_output()
    if damage == "outside_recording":
        output["passages"][0]["words"][0]["start"] = -0.5
    elif damage == "outside_passage":
        source["segments"][0]["start"] = 0.9
    else:
        output["passages"][0]["words"][2]["start"] = 0.2
    with pytest.raises(ValueError):
        enhancement.merge_output(
            source,
            output,
            None,
            enhancement.EnhancementOptions(timing_policy="retain-estimates"),
            2,
            "scope",
        )


@pytest.mark.parametrize("policy", ["retain-estimates", "legacy-score-filter"])
def test_remote_accepts_only_the_explicit_known_policy(policy):
    options = {
        "execution": "ucloud",
        "device": "cuda",
        "language": "da",
        "align": True,
        "diarize": False,
        "duration": 2,
        "allow_downloads": False,
        "timing_policy": policy,
    }
    request = {"options": options, "passages": []}
    assert runtime_batch_remote.BatchController._valid_request(request, ["alignment.json"])
    request["options"]["timing_policy"] = "guess"
    assert not runtime_batch_remote.BatchController._valid_request(request, ["alignment.json"])
