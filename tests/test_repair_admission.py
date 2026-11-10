"""Selected snapshots keep exact full-version lineage and cannot enter result-producing paths."""

import pytest
import test_repair_bundles as fixtures

from cbs_transcriber.enhancement import EnhancementOptions
from cbs_transcriber.repair_sources import inspect_repair_source


@pytest.fixture
def study(tmp_path):
    return fixtures.study.__wrapped__(tmp_path)


def params():
    return {
        "realign": True,
        "passage_timing": True,
        "segment_ids": ["1", "4"],
        "enhancement": EnhancementOptions().model_dump(),
        "timing_units": [
            {"ids": ["1"], "start": 2, "end": 5},
            {"ids": ["4"], "start": 9, "end": 12},
        ],
    }


def test_selected_enqueue_preserves_full_historical_hash_and_exact_projection(study):
    store, recording, result, *_ = study
    current = store.recording(recording["id"])
    full = store.freeze_result(recording["id"], result["id"], current["revision"])
    job = store.enqueue_enhancement(
        recording["id"], result["id"], current["revision"], params(), selected_ids=["1", "4"]
    )
    source = job["params"]["source_snapshot"]
    assert source["segments"] == [full["segments"][1], full["segments"][4]]
    assert source["source_version_id"] == full["source_version_id"]
    assert source["source_payload_sha256"] == full["source_payload_sha256"]
    assert inspect_repair_source(store, job["id"])["source_scope"] == "selected"


@pytest.mark.parametrize("ids", [["4", "1"], ["1", "1"], ["absent"], []])
def test_stale_or_invalid_selected_ids_do_not_insert_job(study, ids):
    store, recording, result, *_ = study
    current = store.recording(recording["id"])
    with pytest.raises(ValueError):
        store.enqueue_enhancement(
            recording["id"], result["id"], current["revision"], params(), selected_ids=ids
        )
    with pytest.raises(ValueError):
        store.enqueue_enhancement(
            recording["id"],
            result["id"],
            current["revision"] + 1,
            params(),
            selected_ids=["1", "4"],
        )
    assert not store.all("SELECT id FROM jobs")


@pytest.mark.parametrize(
    "extra",
    [
        {"passage_timing": False},
        {"transcription": True},
        {"runtime_batch_id": "owner"},
        {"ucloud_api": {"batch_plan": {}}},
        {"enhancement": EnhancementOptions(transcribe=True).model_dump()},
        {"enhancement": EnhancementOptions(diarize=True).model_dump()},
    ],
)
def test_selected_projection_cannot_enter_result_producing_or_shared_paths(study, extra):
    store, recording, result, *_ = study
    with pytest.raises(ValueError):
        store.enqueue_enhancement(
            recording["id"],
            result["id"],
            store.recording(recording["id"])["revision"],
            params() | extra,
            selected_ids=["1", "4"],
        )
    assert not store.all("SELECT id FROM jobs")
