"""Automatic local/UCloud enhancement routing; all runtime and UCloud checks are mocked."""

import wave

import pytest
from admission_support import inspect_and_materialize

from cbs_transcriber import enhancement, enhancement_api, features, routing
from cbs_transcriber.enhancement import EnhancementOptions
from cbs_transcriber.providers import Settings
from cbs_transcriber.transcripts import normalize_segments

CLOUD = {"ucloud_api": {"provider_id": "ucloud", "settings": {}, "runtime_id": "r"}}


@pytest.fixture
def checks(monkeypatch):
    state = {"local": True, "cloud": True, "prepared": True, "local_calls": 0, "cloud_calls": 0}

    def local(options):
        state["local_calls"] += 1
        if not state["local"]:
            raise ValueError("Local processing needs WhisperX 3.8.6.")

    def freeze(store, credentials, project_ids, options):
        state["cloud_calls"] += 1
        assert not options.allow_downloads and options.execution == "ucloud"
        if not state["cloud"]:
            raise ValueError("Connect UCloud under Provider Settings first.")
        return options, CLOUD

    monkeypatch.setattr(enhancement, "require_local_runtime", local)
    monkeypatch.setattr(enhancement_api, "freeze", freeze)
    monkeypatch.setattr(routing, "prepared", lambda credentials, params: state["prepared"])
    return state


def decide(minutes, checks=None, **options):
    values = {"execution": "auto", "align": True, "diarize": False} | options
    chosen = EnhancementOptions(**values)
    router = routing.Router(None, None, chosen)
    return router.decide({"duration": minutes * 60, "project_id": "p"}, chosen)


def test_short_word_timing_runs_locally(checks):
    options, extra, reason = decide(5)
    assert options.execution == "local" and extra == {}
    assert "5.0-minute" in reason and "this computer" in reason


def test_long_recording_runs_on_ucloud_gpu(checks):
    options, extra, reason = decide(45)
    assert options.execution == "ucloud" and options.device == "cuda"
    assert extra == CLOUD and "UCloud" in reason and "45-minute" in reason


def test_speaker_labels_use_a_shorter_local_limit(checks):
    assert decide(5, diarize=True)[0].execution == "ucloud"
    assert decide(2, diarize=True)[0].execution == "local"


def test_local_cuda_extends_the_local_limit(checks):
    assert decide(15, diarize=True, device="cuda")[0].execution == "local"
    assert decide(25, diarize=True, device="cuda")[0].execution == "ucloud"


@pytest.mark.parametrize("problem", ["cloud", "prepared"])
def test_long_recording_falls_back_locally_with_reason(checks, problem):
    checks[problem] = False
    options, extra, reason = decide(45)
    assert options.execution == "local" and extra == {}
    assert "slow" in reason and "UCloud unavailable" in reason


def test_automatic_never_prepares_an_unprepared_runtime(checks):
    # ADR 0059: preparation is confirmed only for explicit UCloud work, never by Automatic,
    # even when another part of the request carried a confirmation.
    checks["prepared"] = False
    options, extra, reason = decide(45, prepare_runtime=True)
    assert options.execution == "local" and extra == {} and "not prepared" in reason
    assert options.prepare_runtime is False


def test_short_recording_uses_ucloud_when_local_runtime_is_broken(checks):
    checks["local"] = False
    options, _, reason = decide(2)
    assert options.execution == "ucloud" and "unavailable" in reason


def test_no_usable_place_raises_the_local_error(checks):
    checks["local"] = checks["cloud"] = False
    with pytest.raises(ValueError, match="WhisperX"):
        decide(45)


def test_router_checks_each_place_once_per_request(checks):
    chosen = EnhancementOptions(execution="auto", align=True)
    router = routing.Router(None, None, chosen)
    for minutes in (1, 2, 40, 50):
        router.decide({"duration": minutes * 60, "project_id": "p"}, chosen)
    assert checks["local_calls"] == 1 and checks["cloud_calls"] == 1


def test_followups_route_each_recording_and_record_the_reason(checks):
    provider = {
        "id": "openai",
        "settings": Settings(provider_type="openai", model="whisper-1").model_dump(),
    }
    recordings = [
        {"id": "short", "duration": 120, "project_id": "p"},
        {"id": "long", "duration": 3600, "project_id": "p"},
    ]
    followups = features.prepare_followups(
        None,
        None,
        recordings,
        [provider],
        features.FeatureRequest(word_timing=True, speaker_labels=True),
        EnhancementOptions(execution="auto", align=True, diarize=True),
    )
    short, long = followups["short", "openai"]["params"], followups["long", "openai"]["params"]
    assert short["enhancement"]["execution"] == "local" and "ucloud_api" not in short
    assert long["enhancement"]["execution"] == "ucloud" and long["ucloud_api"]
    assert long["provider_name"] == "UCloud WhisperX enhancement"
    assert "60-minute" in long["routing"]["reason"] and short["routing"]["mode"] == "auto"


def test_enhancement_endpoint_freezes_a_concrete_place_and_lists_reason(client, checks):
    store = client.app.state.store
    project = store.create_project("Routing")
    recording = store.create_recording(project["id"], "short.wav")
    store.execute("UPDATE recordings SET status='ready',duration=90 WHERE id=?", (recording["id"],))
    result = store.add_result(
        recording["id"], normalize_segments([{"text": "Hej.", "start": 0, "end": 1}]), {}, "R", {}
    )
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True)
    with wave.open(str(directory / "original.wav"), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 16000 * 90)
    with store.library_lock():
        response = client.post(
            f"/api/recordings/{recording['id']}/enhancements",
            json={"result_id": result["id"], "revision": 1, "execution": "auto"},
        )
        assert response.status_code == 202, response.text
        job = inspect_and_materialize(store, client.app.state.credentials, [response.json()["id"]])[
            0
        ]
    assert job["params"]["enhancement"]["execution"] == "local"
    listed = next(j for j in client.get("/api/jobs").json() if j["id"] == job["id"])
    assert "1.5-minute" in listed["routing_reason"]


def test_passage_repairs_never_run_on_ucloud(client, checks):
    store = client.app.state.store
    project = store.create_project("Repairs")
    recording = store.create_recording(project["id"], "long.wav")
    store.execute(
        "UPDATE recordings SET status='ready',duration=3600 WHERE id=?", (recording["id"],)
    )
    result = store.add_result(
        recording["id"], normalize_segments([{"text": "Hej.", "start": 0, "end": 1}]), {}, "R", {}
    )
    body = {"result_id": result["id"], "revision": 1, "segment_id": "0"}
    response = client.post(
        f"/api/recordings/{recording['id']}/enhancements", json=body | {"execution": "ucloud"}
    )
    assert response.status_code == 400
    response = client.post(
        f"/api/recordings/{recording['id']}/enhancements", json=body | {"execution": "auto"}
    )
    assert response.status_code == 202, response.text
    assert response.json()["params"]["enhancement"]["execution"] == "local"
    assert checks["cloud_calls"] == 0


@pytest.mark.parametrize("execution", ["ucloud", "auto"])
def test_followups_check_ucloud_with_their_own_speaker_need(checks, monkeypatch, execution):
    # Native speakers + requested word timing: the follow-up only aligns, so a missing
    # speaker model must not block it, even though the dialog sent diarize=True.
    frozen = []
    original = enhancement_api.freeze

    def freeze(store, credentials, project_ids, options):
        frozen.append(options.diarize)
        return original(store, credentials, project_ids, options)

    monkeypatch.setattr(enhancement_api, "freeze", freeze)
    provider = {
        "id": "diarize",
        "settings": Settings(
            provider_type="openai", model="custom", capability="speakers"
        ).model_dump(),
    }
    followups = features.prepare_followups(
        None,
        None,
        [{"id": "long", "duration": 3600, "project_id": "p"}],
        [provider],
        features.FeatureRequest(word_timing=True, speaker_labels=True),
        EnhancementOptions(execution=execution, align=True, diarize=True),
    )
    params = followups["long", "diarize"]["params"]
    assert params["enhancement"]["diarize"] is False and params["ucloud_api"]
    assert frozen == [False]


def test_router_caches_cloud_checks_per_speaker_need(checks):
    chosen = EnhancementOptions(execution="auto", align=True, diarize=True)
    router = routing.Router(None, None, chosen)
    recording = {"duration": 3600, "project_id": "p"}
    router.decide(recording, chosen.model_copy(update={"diarize": False}))
    router.decide(recording, chosen)
    router.decide(recording, chosen)
    assert checks["cloud_calls"] == 2


def test_researcher_rules_change_limits_and_are_validated(client, checks):
    store = client.app.state.store
    assert client.get("/api/enhancement-routing").json()["rules"] == routing.Rules().model_dump()
    saved = client.put(
        "/api/enhancement-routing",
        json={"word_minutes": 2, "speaker_minutes": 0, "cuda_factor": 3, "fallback": "local"},
    )
    assert saved.status_code == 200
    assert saved.json()["local_minutes"] == {"words": 2, "speakers": 0}
    chosen = EnhancementOptions(execution="auto", align=True)
    router = routing.Router(store, None, chosen)
    assert router.decide({"duration": 90, "project_id": "p"}, chosen)[0].execution == "local"
    options, _, reason = router.decide({"duration": 180, "project_id": "p"}, chosen)
    assert options.execution == "ucloud" and "longer than 2 min" in reason
    # A zero limit sends every speaker run to UCloud when available.
    speakers = chosen.model_copy(update={"diarize": True})
    assert router.decide({"duration": 30, "project_id": "p"}, speakers)[0].execution == "ucloud"
    for bad in ({"word_minutes": -1}, {"cuda_factor": 0.5}, {"fallback": "maybe"}, {"x": 1}):
        assert client.put("/api/enhancement-routing", json=bad).status_code == 422
    assert routing.rules(store).word_minutes == 2


def test_stop_fallback_refuses_long_recordings_without_ucloud(client, checks):
    store = client.app.state.store
    client.put("/api/enhancement-routing", json={"fallback": "stop"})
    checks["cloud"] = False
    chosen = EnhancementOptions(execution="auto", align=True)
    router = routing.Router(store, None, chosen)
    with pytest.raises(ValueError, match="do not run it here"):
        router.decide({"duration": 45 * 60, "project_id": "p"}, chosen)
    # Short recordings still run here under the same rules.
    assert router.decide({"duration": 60, "project_id": "p"}, chosen)[0].execution == "local"
