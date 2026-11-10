"""Repair routing counts selected audio only; every provider/runtime check is mocked."""

import pytest
import test_repair_bundles as fixtures

from cbs_transcriber import enhancement, original_inspection, repair_cloud, repair_remote, routing
from cbs_transcriber.enhancement import EnhancementOptions


@pytest.fixture
def study(tmp_path, monkeypatch):
    value = fixtures.study.__wrapped__(tmp_path)
    monkeypatch.setattr(enhancement, "require_local_runtime", lambda *_: None)
    return value


def selected(study):
    return [{"ids": ["1"], "start": 2, "end": 5}, {"ids": ["4"], "start": 9, "end": 12}]


def test_small_and_single_requests_remain_local_without_cloud_or_source_probe(study, monkeypatch):
    monkeypatch.setattr(
        original_inspection,
        "read",
        lambda *_: pytest.fail("small repair must not inspect source cache"),
    )
    monkeypatch.setattr(
        repair_cloud, "freeze", lambda *_: pytest.fail("small repair must not probe cloud")
    )
    options, extra = routing.repair_route(
        study[0], None, study[1], study[2], selected(study), EnhancementOptions(device="cpu")
    )
    assert options.execution == "local" and "audio_input" not in extra
    routing.save_rules(study[0], routing.Rules(word_minutes=0))
    options, _ = routing.repair_route(
        study[0], None, study[1], study[2], selected(study)[:1], EnhancementOptions(device="cpu")
    )
    assert options.execution == "local"


def test_large_selection_without_cache_defers_bounded_proof_without_media_io(study, monkeypatch):
    routing.save_rules(study[0], routing.Rules(word_minutes=0))
    monkeypatch.setattr(original_inspection, "read", lambda *_: None)
    monkeypatch.setattr(
        repair_cloud,
        "freeze",
        lambda _s, _c, _p, options: (
            options,
            {"repair_cloud": {"protocol": repair_cloud.PROTOCOL}},
        ),
    )
    options, extra = routing.repair_route(
        study[0],
        None,
        study[0].recording(study[1]["id"]),
        study[2],
        selected(study),
        EnhancementOptions(device="cpu"),
    )
    assert options.execution == "ucloud" and "audio_input" not in extra
    assert extra["repair_routing"]["source_policy"] == "selected-original-v1"
    assert extra["repair_source"]["windows"] == selected(study)
    assert extra["repair_source"]["anchor"] is None
    assert "before allocation" in extra["routing"]


def test_large_selection_uses_prepared_cloud_and_freezes_selected_workload(study, monkeypatch):
    routing.save_rules(study[0], routing.Rules(word_minutes=0))
    cache = {
        "state": "ready",
        "receipt": study[-2],
        "generation": "f" * 32,
        "receipt_sha256": repair_remote.digest(repair_remote.encoded(study[-2])),
    }
    monkeypatch.setattr(original_inspection, "read", lambda *_: cache)
    monkeypatch.setattr(
        repair_cloud,
        "freeze",
        lambda _s, _c, _p, options: (
            options.model_copy(update={"device": "cuda"}),
            {"repair_cloud": {"protocol": repair_cloud.PROTOCOL}},
        ),
    )
    options, extra = routing.repair_route(
        study[0], None, study[1], study[2], selected(study), EnhancementOptions(device="cpu")
    )
    assert options.execution == "ucloud" and extra["audio_input"] == study[-2]
    assert extra["repair_routing"]["selected_seconds"] == 6
    assert extra["repair_routing"]["unit_count"] == 2
    assert extra["repair_routing"]["rules"]["word_minutes"] == 0
    assert extra["repair_routing"]["source_policy"] == "original-v1"


def test_cloud_fallback_stop_is_honoured_without_starting_source_inspection(study, monkeypatch):
    routing.save_rules(study[0], routing.Rules(word_minutes=0, fallback="stop"))
    monkeypatch.setattr(original_inspection, "read", lambda *_: None)
    monkeypatch.setattr(
        repair_cloud, "freeze", lambda *_: (_ for _ in ()).throw(ValueError("unavailable"))
    )
    with pytest.raises(ValueError, match="not ready|unavailable"):
        routing.repair_route(
            study[0], None, study[1], study[2], selected(study), EnhancementOptions(device="cpu")
        )


def test_workload_uses_union_and_reports_transferred_audio_separately():
    work = routing.repair_workload(
        [{"ids": ["a"], "start": 0, "end": 10}, {"ids": ["b"], "start": 5, "end": 15}]
    )
    assert work == {
        "unit_count": 2,
        "passage_count": 2,
        "selected_seconds": 15,
        "transfer_seconds": 20,
    }
