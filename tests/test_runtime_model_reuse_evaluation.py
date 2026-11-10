"""Isolated offline measurements; no production scheduling or inference assertions."""

import json
import runpy
import sys
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest

experiment = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "evaluate_runtime_model_reuse.py")
)
OneSlot, evaluate = experiment["OneSlot"], experiment["evaluate"]


def test_actual_worker_constructor_counts_and_exact_stage_bytes():
    result = evaluate(3)
    assert result["baseline"]["model_loads"] == dict.fromkeys(
        ["transcription", "alignment", "diarization"], 3
    )
    assert result["single_slot_reference"]["model_loads"] == dict.fromkeys(
        ["transcription", "alignment", "diarization"], 1
    )
    assert result["single_slot_reference"]["cache_hits"] == 6
    assert result["single_slot_reference"]["cache_evictions"] == 3
    assert result["identical_stage_bytes"] is True
    progress = result["baseline"]["progress"]
    for task in range(3):
        entries = [entry for entry in progress if entry["task"] == task]
        assert [entry["stage"] for entry in entries] == [
            "loading",
            "transcribing",
            "transcribing",
            "aligning",
            "speakers",
            "complete",
        ]
        assert entries[2]["fraction"] == 0.175
        assert entries[-1]["fraction"] == 1
    assert "Hej" not in json.dumps(result)
    assert result["single_slot_reference"]["progress"] == []


def test_late_finite_cohort_requires_another_load_without_changing_outputs():
    result = evaluate(3, cohorts=[2, 1])
    assert result["single_slot_reference"]["model_loads"] == dict.fromkeys(
        ["transcription", "alignment", "diarization"], 2
    )
    assert result["identical_stage_bytes"] is True


@pytest.mark.parametrize("language", ["da", "auto"])
def test_production_resident_loaders_reuse_models_with_original_requests_and_truthful_completion(
    language,
):
    result = evaluate(3, production=True, language=language)
    candidate = result["single_slot_reference"]
    assert candidate["model_loads"] == dict.fromkeys(
        ["transcription", "alignment", "diarization"], 1
    )
    assert candidate["cache_hits"] == 6
    assert result["identical_stage_bytes"] is True
    assert [entry["engine"] for entry in candidate["model_receipts"]] == [
        "faster-whisper",
        "whisperx-align",
        "pyannote",
    ]
    for task in range(3):
        entries = [entry for entry in candidate["progress"] if entry["task"] == task]
        assert sum(entry["stage"] == "complete" for entry in entries) == 1
        assert entries[-1] == {"task": task, "stage": "complete", "fraction": 1}


def test_production_resident_models_are_released_between_finite_late_cohorts():
    result = evaluate(3, cohorts=[2, 1], production=True)
    assert result["single_slot_reference"]["model_loads"] == dict.fromkeys(
        ["transcription", "alignment", "diarization"], 2
    )
    assert result["identical_stage_bytes"] is True


def test_one_slot_drops_previous_identity_before_failed_load():
    slot = OneSlot()
    first = slot.get(("asr", "pin-a", "cuda", "float16", "da"), object)
    assert slot.get(("asr", "pin-a", "cuda", "float16", "da"), object) is first

    def failed():
        assert slot.value is None and slot.identity is None
        raise RuntimeError("synthetic constructor failure")

    with pytest.raises(RuntimeError):
        slot.get(("alignment", "pin-b", "cuda", "float16", "da"), failed)
    assert slot.value is None and slot.identity is None


def test_production_slot_evicts_before_failed_constructor_and_freezes_identity(monkeypatch):
    from cbs_transcriber import enhancement_worker as worker

    released = []
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: True, empty_cache=lambda: released.append(True)
            )
        ),
    )
    receipts = []
    slot = worker.RuntimeModelSlot(lambda identity, ordinal: receipts.append((identity, ordinal)))

    class Model:
        pass

    identity = {"pin": ["repo", "revision-a"], "device": "cuda"}
    model = slot.get(identity, Model)
    previous = weakref.ref(model)
    del model
    identity["pin"][1] = "revision-b"

    def failed():
        assert previous() is None
        assert slot.value is None and slot.identity is None
        raise RuntimeError("Synthetic failed model allocation")

    with pytest.raises(RuntimeError):
        slot.get(identity, failed)
    assert slot.value is None and slot.identity is None
    assert len(receipts) == 1 and len(released) >= 2


def test_production_slot_unloads_ct2_without_cpu_copy_and_refuses_load_after_release_failure():
    from cbs_transcriber import enhancement_worker as worker

    unloads = []
    slot = worker.RuntimeModelSlot(lambda *args: None)

    class Engine:
        def unload_model(self, *, to_cpu):
            unloads.append(to_cpu)
            raise RuntimeError("Synthetic native unload failure")

    slot.get({"pin": "first"}, lambda: SimpleNamespace(model=Engine()))
    with pytest.raises(RuntimeError, match="unload failure"):
        slot.get({"pin": "second"}, lambda: pytest.fail("Loaded before prior native release"))
    assert unloads == [False]
    assert slot.release_failed and slot.value is None
    with pytest.raises(RuntimeError, match="could not be released"):
        slot.get({"pin": "third"}, lambda: pytest.fail("Loaded a poisoned resident slot"))


@pytest.mark.parametrize(
    "files,cohorts", [(0, None), (17, None), (True, None), (3, [2]), (3, [0, 3])]
)
def test_experiment_bounds(files, cohorts):
    with pytest.raises(ValueError):
        evaluate(files, cohorts=cohorts)
