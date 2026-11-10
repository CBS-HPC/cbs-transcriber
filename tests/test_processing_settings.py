"""Provider Settings â†’ Processing: automatic device, cached checks, asked preparation.

ADR 0059."""

import json
import threading
import wave

import pytest
from admission_support import inspect_and_materialize, write_original_wav

from cbs_transcriber import (
    enhancement,
    enhancement_api,
    input_admission,
    media,
    providers,
    runtime_compute,
)
from cbs_transcriber.enhancement import EnhancementOptions, LocalSettings, ProcessingSettings
from cbs_transcriber.store import new_id, now
from cbs_transcriber.transcripts import normalize_segments


def runtime(gpu):
    return {
        "ready": True,
        "version": "3.8.6",
        "expected_version": "3.8.6",
        "gpu": {"checked": True, "available": gpu, "devices": []},
        "note": "Synthetic.",
    }


def test_new_libraries_default_to_automatic_device_and_keep_explicit_choices(client):
    assert LocalSettings().device == "auto" and LocalSettings().download_models is False
    saved = client.get("/api/enhancement-settings").json()
    assert saved["device"] == "auto"
    put = client.put(
        "/api/enhancement-settings",
        json={**saved, "device": "cpu", "download_models": True},
    )
    assert put.status_code == 200
    assert put.json()["device"] == "cpu" and put.json()["download_models"] is True


@pytest.mark.parametrize(
    "stored",
    [None, {}, {"device": "cpu"}, {"download_models": False}, {"prepare_ucloud_models": False}],
)
def test_processing_defaults_fill_only_unset_choices_without_preparing(client, monkeypatch, stored):
    store = client.app.state.store
    if stored is not None:
        store.execute("INSERT INTO settings(id,value) VALUES (3,?)", (json.dumps(stored),))
    before = store.all("SELECT * FROM settings ORDER BY id")

    def refuse(*args, **kwargs):
        pytest.fail("Reading or saving Processing settings must not prepare or check models")

    monkeypatch.setattr(enhancement, "check_runtime", refuse)
    monkeypatch.setattr(enhancement_api, "preparation_states", refuse)
    monkeypatch.setattr(enhancement_api.native, "client_for", refuse)
    monkeypatch.setattr("huggingface_hub.snapshot_download", refuse)
    monkeypatch.setattr("socket.socket.connect", refuse)
    defaults = ProcessingSettings()
    assert defaults.download_models is True and defaults.prepare_ucloud_models is True
    settings = client.get("/api/enhancement-settings").json()
    downloads = (stored or {}).get("download_models", True)
    preparation = (stored or {}).get("prepare_ucloud_models", True)
    assert settings["download_models"] is downloads
    assert settings["prepare_ucloud_models"] is preparation
    assert client.get("/api/local-runtime").json()["status"] is None
    assert store.all("SELECT * FROM settings ORDER BY id") == before
    response = client.put("/api/enhancement-settings", json={"device": "cpu"})
    assert response.status_code == 200, response.text
    assert response.json()["download_models"] is downloads
    assert response.json()["prepare_ucloud_models"] is preparation


@pytest.mark.parametrize("downloads", [False, True])
@pytest.mark.parametrize("preparation", [False, True])
def test_saved_preparation_choices_survive_reads_and_omitted_saves(client, downloads, preparation):
    response = client.put(
        "/api/enhancement-settings",
        json={"download_models": downloads, "prepare_ucloud_models": preparation},
    )
    assert response.status_code == 200, response.text
    for settings in (
        response.json(),
        client.get("/api/enhancement-settings").json(),
        client.put("/api/enhancement-settings", json={"device": "cpu"}).json(),
    ):
        assert settings["download_models"] is downloads
        assert settings["prepare_ucloud_models"] is preparation


@pytest.mark.parametrize("frozen", [{}, {"download_models": False}, {"download_models": True}])
def test_processing_defaults_do_not_reinterpret_frozen_job_options(client, frozen):
    before = EnhancementOptions.model_validate(frozen).model_dump_json()
    response = client.put("/api/enhancement-settings", json={})
    assert response.status_code == 200, response.text
    options = EnhancementOptions.model_validate_json(before)
    assert options.model_dump_json() == before
    assert options.download_models is frozen.get("download_models", False)
    assert options.allow_downloads is False and options.prepare_runtime is False
    assert "prepare_ucloud_models" not in options.model_dump()


def test_runtime_check_is_cached_and_reported_without_a_new_check(client, monkeypatch):
    calls = []

    def check(settings, **kw):
        calls.append(kw)
        return runtime(True)

    monkeypatch.setattr(enhancement, "check_runtime", check)
    assert client.get("/api/local-runtime").json()["status"] is None
    checked = client.post("/api/enhancement-runtime/check", json={}).json()
    assert checked["gpu"]["available"] is True and len(calls) == 1
    status = client.get("/api/local-runtime").json()
    # The automatic device uses the GPU the last check found; reading it starts no check.
    assert status["effective_device"] == "cuda" and len(calls) == 1


def test_a_failed_check_is_not_cached_as_no_gpu(monkeypatch):
    """A slow first import must not pin Automatic to the CPU for the whole session."""
    calls = []

    def check(settings, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("The local Python runtime check failed or timed out.")
        return runtime(True)

    monkeypatch.setattr(enhancement, "check_runtime", check)
    settings = LocalSettings()
    assert enhancement.hardware_status(settings)["ready"] is False
    assert enhancement.cached_hardware(settings) is None
    assert enhancement.detected_device(settings) == "cuda" and len(calls) == 2
    assert enhancement.detected_device(settings) == "cuda" and len(calls) == 2


def test_automatic_device_is_resolved_once_and_recorded_on_the_job(client, monkeypatch):
    monkeypatch.setattr(enhancement, "check_runtime", lambda settings, **kw: runtime(False))
    store = client.app.state.store
    project = store.create_project("Device")
    recording = store.create_recording(project["id"], "a.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=4 WHERE id=?", (rid,))
    result = store.add_result(
        rid,
        normalize_segments([{"text": "Hej verden.", "start": 0, "end": 2}]),
        {},
        "Word model",
        {"capability": "words"},
    )
    response = client.post(
        f"/api/recordings/{rid}/passage-timing",
        json={"result_id": result["id"], "revision": 1, "segment_ids": ["0"]},
    )
    assert response.status_code == 202, response.text
    job = store.job(response.json()["id"])
    assert job["params"]["enhancement"]["device"] == "auto"
    seen = []

    def align(folder, options, token, cancel, progress, **kw):
        seen.append(options.device)
        words = [
            {"word": "Hej", "start": 0.1, "end": 0.5, "score": 0.9},
            {"word": "verden.", "start": 0.6, "end": 1.0, "score": 0.9},
        ]
        enhancement.write_json(
            folder / "alignment.json", {"passages": [{"id": "0", "words": words}]}
        )

    def ffmpeg(args, cancel):
        # Timing capture binds the prepared WAV's real header (16 kHz mono silence).
        with wave.open(str(args[-1]), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\0\0" * 16000 * 4)

    monkeypatch.setattr(media, "ffmpeg", ffmpeg)
    monkeypatch.setattr(enhancement, "run_worker", align)
    queue = client.app.state.queue
    queue.cancel_event = threading.Event()
    # Workers process claimed jobs; artifact capture only writes to a running owner.
    store.update_job(job["id"], state="running")
    queue.process(job["id"])
    assert seen == ["cpu"]
    assert store.job(job["id"])["params"]["enhancement"]["device"] == "cpu"


def test_explicit_ucloud_enhancement_on_an_unprepared_runtime_asks_first(
    client, monkeypatch, credentials
):
    from cbs_transcriber import enhancement_api

    client.put("/api/enhancement-settings", json={"prepare_ucloud_models": False})
    store = client.app.state.store
    project = store.create_project("Prepare")
    recording = store.create_recording(project["id"], "a.wav")
    rid = recording["id"]
    write_original_wav(store, recording)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (rid,))
    result = store.add_result(
        rid, normalize_segments([{"text": "Hej.", "start": 0, "end": 2}]), {}, "Model", {}
    )
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="large-v3",
        ucloud_transcriber={
            "project_id": "synthetic-project",
            "storage_path": "/synthetic-drive/cbs",
            "application": {"name": "transcriber/Batch", "version": "synthetic"},
            "product": {"id": "gpu-nvidia-b200-1-gpu", "category": "gpu", "provider": "synthetic"},
        },
    )
    identifier = new_id()
    store.execute(
        "INSERT INTO providers VALUES (?,?,?,?)",
        (identifier, "UCloud", settings.model_dump_json(), now()),
    )
    credentials.set(providers.credential_scope(settings), "synthetic-token", False)
    monkeypatch.setattr(
        runtime_compute,
        "freeze",
        lambda *_: {
            "runtime_product": runtime_compute.MIG_PRODUCT,
            "runtime_compute_policy": runtime_compute.POLICY,
        },
    )
    monkeypatch.setattr("cbs_transcriber.runtime_batches.freeze_plan", lambda *_: None)
    monkeypatch.setattr(enhancement_api, "runtime_stamped", lambda credentials, chosen: False)
    body = {"result_id": result["id"], "revision": 1, "execution": "ucloud"}
    with store.library_lock():
        accepted = client.post(f"/api/recordings/{rid}/enhancements", json=body)
        assert accepted.status_code == 202, accepted.text
        failed = inspect_and_materialize(store, credentials, [accepted.json()["id"]])[0]
        assert failed["state"] == "failed"
        assert failed["params"]["input_intent"]["error"]["code"] == "needs_confirmation"
        preview = input_admission.projection(store, failed["id"])["prepare_preview"]
        intent = failed["params"]["input_intent"]
        confirmed = client.post(
            f"/api/input-intents/{failed['id']}/prepare",
            json={
                "epoch": intent["epoch"],
                "phase": intent["phase"],
                "fingerprint": preview["fingerprint"],
                "confirmed": True,
            },
        )
        assert confirmed.status_code == 202, confirmed.text
        prepared = inspect_and_materialize(store, credentials, [confirmed.json()["jobs"][0]["id"]])[
            0
        ]
        assert prepared["params"]["ucloud_api"]["prepare_runtime"] is True
        # A connection failure makes no readiness assertion: the owned job still checks.
        store.update_job(prepared["id"], state="completed")
        monkeypatch.setattr(enhancement_api, "runtime_stamped", lambda credentials, chosen: None)
        unavailable = client.post(f"/api/recordings/{rid}/enhancements", json=body)
        assert unavailable.status_code == 202, unavailable.text
        queued = inspect_and_materialize(store, credentials, [unavailable.json()["id"]])[0]
        assert queued["state"] == "queued"
        assert queued["params"]["ucloud_api"]["prepare_runtime"] is False
