"""Fresh original-input policies, legacy recovery and real local preparation seams."""

import copy
import json
import threading

import pytest
from admission_support import inspect_and_materialize
from conftest import finish_media

from cbs_transcriber import enhancement, media, providers
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def library_lease(client):
    with client.app.state.store.library_lock():
        yield


def ready_recording(client, wav_bytes):
    project = client.post("/api/projects", json={"name": "Synthetic original input"}).json()
    response = client.post(
        "/api/recordings",
        data={"project_id": project["id"]},
        files={"file": ("synthetic.wav", wav_bytes, "audio/wav")},
    )
    assert response.status_code == 201, response.text
    return finish_media(client, response.json())


def custom_provider(client, *, approved_for=None):
    response = client.put(
        "/api/settings",
        json={
            "provider_type": "custom",
            "base_url": "http://127.0.0.1:9000/v1",
            "model": "synthetic",
            "capability": "segments",
            **({"approved_for": approved_for} if approved_for is not None else {}),
        },
    )
    assert response.status_code == 200, response.text


def fresh_job(client, recording):
    response = client.post("/api/transcriptions", json={"recording_ids": [recording["id"]]})
    assert response.status_code == 202, response.text
    return inspect_and_materialize(
        client.app.state.store, client.app.state.credentials, [response.json()[0]["id"]]
    )[0]


def test_fresh_asr_freezes_original_without_changing_recording_duration(
    client, wav_bytes, library_lease
):
    recording = ready_recording(client, wav_bytes)
    custom_provider(client)
    store = client.app.state.store
    # The old value is a container observation, not the selected stream's measured duration.
    store.execute("UPDATE recordings SET duration=4 WHERE id=?", (recording["id"],))
    job = fresh_job(client, recording)
    assert job["params"]["audio_input"]["policy"] == "original-v1"
    assert job["params"]["audio_input"]["duration"] == 3
    assert store.recording(recording["id"])["duration"] == 4


def test_fresh_api_prepares_original_with_broken_playback_and_reuses_frozen_plan(
    client, wav_bytes, monkeypatch, library_lease
):
    recording = ready_recording(client, wav_bytes)
    custom_provider(client)
    store, queue = client.app.state.store, client.app.state.queue
    directory = store.directory(recording["id"])
    (directory / "audio.mp3").write_bytes(b"This is not playable audio.")
    job = fresh_job(client, recording)
    frozen = copy.deepcopy(job["params"]["audio_input"])
    calls = []

    def transcribe(path, settings, key):
        assert path.is_file() and path.stat().st_size > 0
        calls.append(path)
        return {"text": "Synthetic wording.", "segments": [{"text": "Synthetic wording."}]}

    monkeypatch.setattr(providers, "transcribe_file", transcribe)
    store.update_job(job["id"], state="running")
    queue.process(job["id"])
    assert len(calls) == 1
    saved = store.results(recording["id"])[0]
    version = store.all("SELECT context FROM result_versions WHERE result_id=?", (saved["id"],))[0]
    assert json.loads(version["context"])["audio_input"] == frozen
    # A repeat process uses preserved response/plan bytes and performs no second request.
    queue.process(job["id"])
    assert len(calls) == 1
    assert store.job(job["id"])["params"]["audio_input"] == frozen
    assert len(store.results(recording["id"])) == 1
    # A self-consistent replacement must not rebind existing response checkpoints.
    before = store.one("SELECT params FROM jobs WHERE id=?", (job["id"],))["params"]
    changed = copy.deepcopy(store.job(job["id"])["params"]["audio_preparations"][0])
    changed["parts"][0]["prepared"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="different prepared audio"):
        store.record_audio_preparation(job["id"], changed)
    assert store.one("SELECT params FROM jobs WHERE id=?", (job["id"],))["params"] == before


def test_official_openai_fitting_original_is_sent_without_copy_or_conversion(
    client, wav_bytes, monkeypatch, library_lease
):
    recording = ready_recording(client, wav_bytes)
    response = client.put(
        "/api/settings",
        json={"provider_type": "openai", "model": "whisper-1", "api_key": "synthetic-not-a-key"},
    )
    assert response.status_code == 200, response.text
    job = fresh_job(client, recording)
    assert job["params"]["audio_transport"] == "original"
    store, queue = client.app.state.store, client.app.state.queue
    original = store.directory(recording["id"]) / "original.wav"
    paths = []

    def transcribe(path, settings, key):
        paths.append(path)
        assert path == original
        return {"text": "Synthetic.", "segments": [{"text": "Synthetic."}]}

    monkeypatch.setattr(providers, "transcribe_file", transcribe)
    store.update_job(job["id"], state="running")
    queue.process(job["id"])
    assert paths == [original]
    folder = original.parent / "chunks" / job["id"]
    assert not (folder / "0000.mp3").exists()
    assert original.read_bytes() == wav_bytes


def test_protected_approval_is_rechecked_after_prepared_path_validation(
    client, wav_bytes, monkeypatch, library_lease
):
    from cbs_transcriber import audio_inputs

    recording = ready_recording(client, wav_bytes)
    custom_provider(client, approved_for=["protected"])
    store, queue = client.app.state.store, client.app.state.queue
    store.execute(
        "UPDATE projects SET classification='protected' WHERE id=?", (recording["project_id"],)
    )
    job = fresh_job(client, recording)
    resolve = audio_inputs.chunk_path

    def revoke(*args):
        path = resolve(*args)
        if not store.job(job["id"])["params"].get("audio_preparations"):
            return path  # Revoke during the final upload-path check, after preparation.
        row = store.one("SELECT value FROM providers WHERE id=?", (job["params"]["provider_id"],))
        settings = json.loads(row["value"])
        settings["approved_for"] = []
        store.execute(
            "UPDATE providers SET value=? WHERE id=?",
            (json.dumps(settings), job["params"]["provider_id"]),
        )
        return path

    def unexpected_upload(*args):
        raise AssertionError("A revoked provider must receive no private audio.")

    monkeypatch.setattr(audio_inputs, "chunk_path", revoke)
    monkeypatch.setattr(providers, "transcribe_file", unexpected_upload)
    with pytest.raises(ValueError, match="explicitly approved"):
        queue.process(job["id"])
    assert store.job(job["id"])["chunks"] == []


def test_fresh_enhancement_and_followup_freeze_policy_but_legacy_insert_does_not(
    client, wav_bytes, monkeypatch, library_lease
):
    recording = ready_recording(client, wav_bytes)
    custom_provider(client)
    store = client.app.state.store
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Synthetic source.", "start": 0, "end": 2}]),
        {},
        "Synthetic",
        {},
    )
    monkeypatch.setattr(enhancement, "require_local_runtime", lambda *a, **kw: None)
    monkeypatch.setattr("cbs_transcriber.app.require_local_runtime", lambda *a, **kw: None)
    response = client.post(
        f"/api/recordings/{recording['id']}/enhancements",
        json={"result_id": result["id"], "revision": 1, "execution": "local", "diarize": False},
    )
    assert response.status_code == 202, response.text
    job = inspect_and_materialize(store, client.app.state.credentials, [response.json()["id"]])[0]
    assert job["params"]["audio_input"]["policy"] == "original-v1"
    store.update_job(job["id"], state="cancelled")
    legacy = store.add_job(recording["id"], "enhance", {"enhancement": {"execution": "local"}})
    assert "audio_input" not in legacy["params"]


def test_legacy_chunk_preparation_keeps_playback_source(tmp_path, monkeypatch):
    commands = []

    def ffmpeg(args, cancel):
        commands.append(args)
        (tmp_path / "chunks" / "legacy" / "0000.mp3").write_bytes(b"synthetic")
        return ""

    monkeypatch.setattr(media, "ffmpeg", ffmpeg)
    folder, plan = media.prepare_chunks(tmp_path, "legacy", 3, 20, threading.Event())
    assert commands[0][commands[0].index("-i") + 1] == str(tmp_path / "audio.mp3")
    assert json.loads((folder / "plan.json").read_text()) == plan


def test_fresh_followup_policy_is_frozen_before_result_commit(
    client, wav_bytes, monkeypatch, library_lease
):
    recording = ready_recording(client, wav_bytes)
    custom_provider(client)
    monkeypatch.setattr(enhancement, "require_local_runtime", lambda *a, **kw: None)
    response = client.post(
        "/api/transcriptions",
        json={
            "recording_ids": [recording["id"]],
            "requested_features": {"word_timing": True},
            "enhancement": {"execution": "local", "diarize": False},
        },
    )
    assert response.status_code == 202, response.text
    parent = inspect_and_materialize(
        client.app.state.store, client.app.state.credentials, [response.json()[0]["id"]]
    )[0]
    expected = parent["params"]["audio_input"]
    followup = parent["params"]["followup"]
    assert followup["params"]["audio_input"] == expected
    store = client.app.state.store
    store.update_job(parent["id"], state="running")
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "Synthetic provider output."}]),
        {},
        "Synthetic",
        {"kind": "provider"},
        job_id=parent["id"],
    )
    assert store.job(followup["id"])["params"]["audio_input"] == expected


def test_late_legacy_followup_keeps_legacy_policy(client, wav_bytes):
    recording = ready_recording(client, wav_bytes)
    store = client.app.state.store
    from cbs_transcriber.store import new_id

    child_id = new_id()
    parent = store.add_job(
        recording["id"],
        "transcribe",
        {"followup": {"id": child_id, "params": {"enhancement": {"execution": "local"}}}},
    )
    store.update_job(parent["id"], state="running")
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "Legacy frozen output."}]),
        {},
        "Legacy",
        {"kind": "provider"},
        job_id=parent["id"],
    )
    assert "audio_input" not in store.job(child_id)["params"]


def test_new_enhancement_wav_uses_original_and_frozen_effective_duration(
    client, wav_bytes, monkeypatch
):
    from cbs_transcriber import audio_inputs

    recording = ready_recording(client, wav_bytes)
    store, queue = client.app.state.store, client.app.state.queue
    snapshot = audio_inputs.freeze(store, recording)
    store.execute("UPDATE recordings SET duration=4 WHERE id=?", (recording["id"],))
    (store.directory(recording["id"]) / "audio.mp3").write_bytes(b"broken playback")
    options = enhancement.EnhancementOptions(execution="local", transcribe=True, diarize=False)
    job = store.add_job(
        recording["id"],
        "enhance",
        {"enhancement": options.model_dump(), "audio_input": snapshot, "transcription": True},
    )
    monkeypatch.setattr(enhancement, "require_local_runtime", lambda *a, **kw: None)

    def worker(folder, options, token, cancel, progress, **kwargs):
        request = json.loads((folder / "request.json").read_text())
        assert request["options"]["duration"] == 3
        enhancement.write_json(
            folder / "transcription.json",
            {"segments": [{"text": "Synthetic.", "start": 0, "end": 2}]},
        )
        enhancement.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {"id": "0", "words": [{"word": "Synthetic.", "start": 0, "end": 2, "score": 1}]}
                ]
            },
        )

    monkeypatch.setattr(enhancement, "run_worker", worker)
    store.update_job(job["id"], state="running")
    queue.process(job["id"])
    assert store.recording(recording["id"])["duration"] == 4
    assert store.job(job["id"])["params"]["audio_preparations"]


def test_selected_passage_repair_keeps_bounded_legacy_contract(client, wav_bytes, monkeypatch):
    recording = ready_recording(client, wav_bytes)
    store, queue = client.app.state.store, client.app.state.queue
    result = store.add_result(
        recording["id"],
        normalize_segments(
            [
                {"text": "Fix this.", "start": 0.5, "end": 1.5},
                {"text": "Keep this.", "start": 2, "end": 2.5},
            ]
        ),
        {},
        "Synthetic",
        {},
    )
    before = copy.deepcopy(result["segments"][1])
    monkeypatch.setattr("cbs_transcriber.app.require_local_runtime", lambda *a, **kw: None)
    monkeypatch.setattr(enhancement, "require_local_runtime", lambda *a, **kw: None)
    calls = []
    execute = media.ffmpeg

    def observe(args, *a, **kw):
        calls.append(args)
        return execute(args, *a, **kw)

    monkeypatch.setattr(media, "ffmpeg", observe)
    response = client.post(
        f"/api/recordings/{recording['id']}/enhancements",
        json={
            "result_id": result["id"],
            "revision": 1,
            "segment_ids": ["0"],
            "execution": "local",
            "diarize": False,
        },
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert "audio_input" not in job["params"]

    def worker(folder, options, token, cancel, progress, **kw):
        enhancement.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": "0",
                        "words": [
                            {"word": "Fix", "start": 0, "end": 0.4, "score": 1},
                            {"word": "this.", "start": 0.5, "end": 1, "score": 1},
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(enhancement, "run_worker", worker)
    store.update_job(job["id"], state="running")
    queue.process(job["id"])
    decodes = [args for args in calls if "pcm_s16le" in args]
    assert len(decodes) == 1
    assert float(decodes[0][decodes[0].index("-ss") + 1]) == 0.5
    assert float(decodes[0][decodes[0].index("-t") + 1]) == 1
    assert decodes[0][decodes[0].index("-i") + 1].endswith("audio.mp3")
    saved = store.one("SELECT id FROM transcript_results WHERE job_id=?", (job["id"],))
    assert store.result(recording["id"], saved["id"])["segments"][1] == before
