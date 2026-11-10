import sys
import threading

import pytest
from admission_support import inspect_and_materialize, write_original_wav

from cbs_transcriber import enhancement, media
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def source(client, monkeypatch):
    monkeypatch.setattr(enhancement, "check_runtime", lambda options, **kw: {"ready": False})
    store = client.app.state.store
    project = store.create_project("Runtime preflight")
    recording = store.create_recording(project["id"], "synthetic.wav")
    write_original_wav(store, recording)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Keep this wording.", "start": 0, "end": 3}]),
        {},
        "Reviewed",
        {},
    )
    with store.library_lock():
        yield recording["id"], result["id"]


def test_unprepared_local_runtime_fails_admission_before_executable_job(
    client, source, monkeypatch
):
    rid, result_id = source
    checks = []
    require_runtime = enhancement.require_local_runtime

    def check(options):
        checks.append(options.runtime_python)
        return require_runtime(options)

    monkeypatch.setattr(enhancement, "require_local_runtime", check)
    response = client.post(
        f"/api/recordings/{rid}/enhancements",
        json={
            "result_id": result_id,
            "revision": 1,
            "runtime_python": sys.executable,
        },
    )
    assert response.status_code == 202, response.text
    accepted = response.json()
    store = client.app.state.store
    assert accepted["params"]["input_intent"]["phase"] == "waiting"
    [job] = inspect_and_materialize(store, client.app.state.credentials, [accepted["id"]])
    assert job["state"] == "failed"
    assert job["params"]["input_intent"]["error"]["code"] == "preparation_failed"
    assert "input_acceptance" not in job["params"] and "audio_input" not in job["params"]
    assert store.claim_job("enhance") is None
    assert len(store.results(rid)) == 1
    assert checks == [sys.executable]


def legacy_job(client, source):
    rid, result_id = source
    store = client.app.state.store
    job = store.add_job(
        rid,
        "enhance",
        {
            "enhancement": enhancement.EnhancementOptions().model_dump(),
            "source_snapshot": store.freeze_result(rid, result_id, 1),
        },
    )
    return job


def test_retry_with_unprepared_frozen_runtime_stays_failed(client, source):
    job = legacy_job(client, source)
    store = client.app.state.store
    store.update_job(job["id"], state="failed")
    response = client.post(f"/api/jobs/{job['id']}/retry", json={})
    assert response.status_code == 400, response.text
    assert "original Python runtime" in response.json()["detail"]
    assert store.job(job["id"])["state"] == "failed"
    assert store.job(job["id"])["params"] == job["params"]


def test_legacy_worker_checks_runtime_before_audio_conversion(client, source, monkeypatch):
    job = legacy_job(client, source)
    monkeypatch.setattr(
        media, "ffmpeg", lambda *args: pytest.fail("converted before runtime check")
    )
    queue = client.app.state.queue
    queue.cancel_event = threading.Event()
    with pytest.raises(ValueError, match="WhisperX"):
        queue.process(job["id"])
    assert len(client.app.state.store.results(source[0])) == 1


@pytest.mark.parametrize("during_probe", ["unchanged", "review_edit", "history_corruption"])
def test_runtime_probe_releases_claim_lock_and_rechecks_frozen_source(
    client, source, monkeypatch, during_probe
):
    rid, result_id = source
    queue = client.app.state.queue
    store = client.app.state.store

    def probe(options, *, hardware):
        assert hardware is False
        claimed = threading.Event()

        def claim():
            with queue.lock:
                claimed.set()

        thread = threading.Thread(target=claim, daemon=True)
        thread.start()
        assert claimed.wait(2), "Runtime probe held the worker claim lock"
        thread.join()
        if during_probe == "review_edit":
            edited = client.patch(
                f"/api/recordings/{rid}/segments/0",
                json={"revision": 1, "mode": "free", "text": "New reviewed wording."},
            )
            assert edited.status_code == 200, edited.text
        elif during_probe == "history_corruption":
            version = store.result_versions(rid, result_id)[0]
            store.execute(
                "UPDATE result_versions SET payload_sha256=? WHERE id=?",
                ("f" * 64, version["id"]),
            )
        return {"ready": True}

    monkeypatch.setattr(enhancement, "check_runtime", probe)
    response = client.post(
        f"/api/recordings/{rid}/enhancements",
        json={"result_id": result_id, "revision": 1},
    )
    assert response.status_code == 202, response.text
    assert store.recording(rid)["revision"] == 1
    accepted = response.json()
    frozen_source = accepted["params"]["input_intent"]["source_snapshot"]
    [job] = inspect_and_materialize(store, client.app.state.credentials, [accepted["id"]])
    assert job["state"] == ("failed" if during_probe == "history_corruption" else "queued")
    if during_probe == "history_corruption":
        assert job["params"]["input_intent"]["error"]["code"] == "source_invalid"
        assert "input_acceptance" not in job["params"] and "audio_input" not in job["params"]
        assert store.claim_job("enhance") is None
    else:
        assert "input_acceptance" in job["params"] and "input_intent" not in job["params"]
        assert job["params"]["source_snapshot"] == frozen_source
        assert store.recording(rid)["segments"][0]["text"] == (
            "New reviewed wording." if during_probe == "review_edit" else "Keep this wording."
        )
        assert store.recording(rid)["revision"] == (2 if during_probe == "review_edit" else 1)
    assert len(store.all("SELECT id FROM jobs WHERE kind='enhance'")) == 1
    assert len(store.all("SELECT id FROM jobs WHERE kind='source-inspect'")) == 1


def test_cuda_preflight_requires_accessible_gpu(monkeypatch):
    def probe(options, *, hardware):
        assert hardware is True
        return {"ready": True, "gpu": {"available": False}}

    monkeypatch.setattr(enhancement, "check_runtime", probe)
    with pytest.raises(ValueError, match="cannot use CUDA"):
        enhancement.require_local_runtime(enhancement.EnhancementOptions(device="cuda"))
