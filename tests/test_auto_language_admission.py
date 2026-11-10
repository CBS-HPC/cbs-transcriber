"""Internal full-ASR recovery support must not admit unsupported public Auto jobs."""

import pytest

from cbs_transcriber import enhancement, enhancement_api, features, providers
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def protected_boundary(client, monkeypatch):
    client.put("/api/settings", json={"api_key": "synthetic", "remember": False})
    store = client.app.state.store
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Reviewed æøå.", "start": 0, "end": 3}]),
        {},
        "Reviewed",
        {},
    )

    def refuse(*args, **kwargs):
        pytest.fail("Unsupported Auto must fail before runtime, network or paid ASR checks")

    for module, name in (
        (enhancement, "check_runtime"),
        (enhancement, "require_local_runtime"),
        (enhancement_api, "freeze"),
        (enhancement_api, "whisperx_ready"),
        (providers, "transcribe_file"),
    ):
        monkeypatch.setattr(module, name, refuse)
    return recording["id"], result["id"]


@pytest.mark.parametrize("requested", [None, {"word_timing": True}, {"speaker_labels": True}])
def test_v124_public_auto_followups_refuse_before_parent_asr(client, protected_boundary, requested):
    rid, result_id = protected_boundary
    before = client.app.state.store.recording(rid)
    # Full-ASR options are valid for frozen internal readers, but not for source follow-ups.
    options = enhancement.EnhancementOptions(language="auto", transcribe=True)
    response = client.post(
        "/api/transcriptions",
        json={
            "recording_ids": [rid],
            "replace": True,
            "requested_features": requested,
            "enhancement": options.model_dump(),
        },
    )
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "Invalid request fields. Check the supplied values."}
    assert client.get("/api/jobs").json() == []
    assert client.app.state.store.recording(rid) == before
    assert client.app.state.store.recording(rid)["active_transcript_id"] == result_id


def test_v124_public_existing_source_auto_refuses_before_runtime(client, protected_boundary):
    rid, result_id = protected_boundary
    response = client.post(
        f"/api/recordings/{rid}/enhancements",
        json={
            "result_id": result_id,
            "revision": client.app.state.store.recording(rid)["revision"],
            "language": "auto",
            "transcribe": True,
        },
    )
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "Invalid request fields. Check the supplied values."}
    assert client.get("/api/jobs").json() == []
    assert len(client.app.state.store.results(rid)) == 1


@pytest.mark.parametrize("requested", [None, features.FeatureRequest(word_timing=True)])
def test_v124_direct_followup_builder_refuses_even_without_missing_features(
    client, protected_boundary, requested
):
    rid, _ = protected_boundary
    store = client.app.state.store
    options = enhancement.EnhancementOptions(language="auto", transcribe=True)
    with pytest.raises(ValueError, match="Automatic language follow-ups"):
        features.prepare_followups(
            store,
            client.app.state.credentials,
            [store.recording(rid)],
            [providers.profile(store, providers.default_provider_id(store))],
            requested,
            options,
        )
    assert client.get("/api/jobs").json() == []
