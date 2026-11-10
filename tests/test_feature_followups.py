import sqlite3
import sys

import pytest
from admission_support import inspect_and_materialize

from cbs_transcriber import enhancement, enhancement_api, language_preferences, media, providers
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def prepared(client, monkeypatch, wav_bytes):
    monkeypatch.setattr(enhancement, "check_runtime", lambda options, **kw: {"ready": True})
    client.put("/api/settings", json={"api_key": "synthetic", "remember": False})
    store = client.app.state.store
    project = store.create_project("Feature choices")
    recording = store.create_recording(project["id"], "synthetic.wav")
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True)
    (directory / "original.wav").write_bytes(wav_bytes)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    baseline = store.add_result(
        recording["id"], normalize_segments([{"text": "Reviewed baseline."}]), {}, "Reviewed", {}
    )
    calls = []

    def transcribe(*args):
        calls.append(True)
        return {
            "segments": [{"text": "New wording.", "start": 0, "end": 3}],
            "words": [
                {"word": "New", "start": 0, "end": 1.5},
                {"word": "wording", "start": 1.5, "end": 3},
            ],
        }

    monkeypatch.setattr(providers, "transcribe_file", transcribe)
    with store.library_lock():
        yield recording["id"], baseline["id"], calls


def request(rid, **changes):
    return {
        "recording_ids": [rid],
        "replace": True,
        "requested_features": {"word_timing": True, "speaker_labels": True},
        "enhancement": {"runtime_python": sys.executable},
        **changes,
    }


def materialize_response(client, response):
    """Make the accepted background phase explicit before asserting producer behavior."""
    assert response.status_code == 202, response.text
    return inspect_and_materialize(
        client.app.state.store,
        client.app.state.credentials,
        [job["id"] for job in response.json()],
    )


def test_french_speaker_only_followup_keeps_transcription_language(client, prepared):
    rid, _, _ = prepared
    store = client.app.state.store
    language_preferences.save(store, "recording", rid, "fr", 0)
    response = client.post(
        "/api/transcriptions", json=request(rid, requested_features={"speaker_labels": True})
    )
    parent = materialize_response(client, response)[0]
    assert parent["params"]["settings"]["language"] == "fr"
    assert parent["params"]["input_acceptance"]["language_preference"] == {
        "scope": "recording",
        "language": "fr",
    }
    options = parent["params"]["followup"]["params"]["enhancement"]
    assert options["diarize"] is True and options["align"] is False
    assert options["language"] in {"da", "en"}  # unused compatibility option
    client.app.state.queue.process(parent["id"])
    [child] = store.all("SELECT id FROM jobs WHERE kind='enhance'")
    frozen = store.job(child["id"])["params"]["enhancement"]
    assert frozen["diarize"] is True and frozen["align"] is False


def test_french_word_timing_followup_is_still_rejected(client, prepared):
    rid, _, _ = prepared
    store = client.app.state.store
    language_preferences.save(store, "recording", rid, "fr", 0)
    client.put("/api/settings", json={"model": "gpt-4o-transcribe"})
    response = client.post("/api/transcriptions", json=request(rid))
    assert response.status_code == 400, response.text
    assert "Word timing" in response.text


def test_french_speaker_only_enhancement_keeps_preference_through_admission(client, prepared):
    rid, _, _ = prepared
    store = client.app.state.store
    language_preferences.save(store, "recording", rid, "fr", 0)
    result = store.add_result(
        rid,
        normalize_segments([{"text": "Bonjour.", "start": 0, "end": 3}]),
        {},
        "Timed source",
        {},
    )
    response = client.post(
        f"/api/recordings/{rid}/enhancements",
        json={"result_id": result["id"], "revision": 1, "align": False, "diarize": True},
    )
    assert response.status_code == 202, response.text
    accepted = response.json()["params"]["input_intent"]["accepted"]
    assert accepted["language_preference"] == {"scope": "recording", "language": "fr"}
    assert accepted["options"]["language"] in {"da", "en"}  # unused compatibility option
    [job] = inspect_and_materialize(store, client.app.state.credentials, [response.json()["id"]])
    assert job["state"] != "failed", job["message"]
    assert job["params"]["enhancement"]["diarize"] is True
    assert job["params"]["enhancement"]["align"] is False


def test_v46_provider_result_and_followup_are_idempotent_and_keep_reviewed_text(
    client, prepared, monkeypatch
):
    rid, baseline, calls = prepared
    response = client.post("/api/transcriptions", json=request(rid))
    assert response.status_code == 202, response.text
    parent = materialize_response(client, response)[0]
    assert parent["params"]["followup"]["params"]["enhancement"]["align"] is False
    assert parent["params"]["followup"]["params"]["enhancement"]["diarize"] is True
    queue, store = client.app.state.queue, client.app.state.store
    queue.process(parent["id"])
    children = store.all("SELECT id FROM jobs WHERE kind='enhance'")
    assert len(children) == 1
    child = store.job(children[0]["id"])
    assert child["params"]["parent_transcription_id"] == parent["id"]
    assert child["params"]["source_snapshot"]["segments"][0]["text"] == "New wording."
    assert store.recording(rid)["active_transcript_id"] == baseline
    assert Store(store.root).job(child["id"])["params"] == child["params"]
    queue.process(parent["id"])
    assert len(calls) == 1
    assert len(store.all("SELECT id FROM jobs WHERE kind='enhance'")) == 1

    def execute(worker, job, recording):
        source = job["params"]["source_snapshot"]
        segments, speakers = enhancement.merge_output(
            source,
            None,
            {
                "turns": [
                    {"start": 0, "end": 1.5, "speaker": "A"},
                    {"start": 1.5, "end": 3, "speaker": "B"},
                ]
            },
            enhancement.EnhancementOptions.model_validate(job["params"]["enhancement"]),
            3,
            job["id"],
        )
        worker.store.add_result(rid, segments, speakers, "Enhanced", {}, job_id=job["id"])

    monkeypatch.setattr(enhancement, "run_enhancement", execute)
    queue.process(child["id"])
    assert len(store.results(rid)) == 3
    assert store.recording(rid)["active_transcript_id"] == baseline


def test_v45_missing_runtime_or_untimed_speaker_dependency_prevents_asr(
    client, prepared, monkeypatch
):
    rid, _, _ = prepared
    monkeypatch.setattr(enhancement, "check_runtime", lambda options, **kw: {"ready": False})
    response = client.post("/api/transcriptions", json=request(rid))
    failed = materialize_response(client, response)[0]
    assert failed["state"] == "failed"
    assert failed["params"]["input_intent"]["error"]["code"] == "preparation_failed"
    assert "followup" not in failed["params"]

    client.put("/api/settings", json={"model": "gpt-4o-transcribe"})
    response = client.post(
        "/api/transcriptions", json=request(rid, requested_features={"speaker_labels": True})
    )
    failed = materialize_response(client, response)[0]
    assert failed["state"] == "failed"
    assert "followup" not in failed["params"]
    assert not client.app.state.store.all("SELECT id FROM jobs WHERE kind='enhance'")
    assert prepared[2] == []


def test_batch_cuda_preflight_rejects_unavailable_gpu_before_asr(client, prepared, monkeypatch):
    rid, _, calls = prepared
    monkeypatch.setattr(
        enhancement,
        "check_runtime",
        lambda options, **kw: {
            "ready": True,
            "gpu": {"checked": True, "available": False, "devices": []},
        },
    )
    response = client.post(
        "/api/transcriptions",
        json=request(
            rid,
            enhancement={"runtime_python": sys.executable, "device": "cuda"},
        ),
    )
    failed = materialize_response(client, response)[0]
    assert failed["state"] == "failed"
    assert failed["params"]["input_intent"]["error"]["code"] == "preparation_failed"
    assert calls == []
    assert not client.app.state.store.all("SELECT id FROM jobs WHERE kind='enhance'")


def test_v46_atomic_followup_failure_rolls_back_provider_result(client, prepared):
    rid, _, calls = prepared
    parent = materialize_response(client, client.post("/api/transcriptions", json=request(rid)))[0]
    store = client.app.state.store
    with store.connection() as db:
        db.execute(
            "CREATE TRIGGER reject_followup BEFORE INSERT ON jobs WHEN NEW.kind='enhance' "
            "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        client.app.state.queue.process(parent["id"])
    assert len(store.results(rid)) == 1
    assert not store.all("SELECT id FROM jobs WHERE kind='enhance'")
    store.execute("DROP TRIGGER reject_followup")
    client.app.state.queue.process(parent["id"])
    assert len(store.results(rid)) == 2
    assert len(store.all("SELECT id FROM jobs WHERE kind='enhance'")) == 1
    assert len(calls) == 1


def test_v46_cancel_parent_cancels_only_its_followup(client, prepared):
    rid, _, _ = prepared
    parent = materialize_response(client, client.post("/api/transcriptions", json=request(rid)))[0]
    queue, store = client.app.state.queue, client.app.state.store
    queue.process(parent["id"])
    unrelated = store.add_job(rid, "enhance", {"source_snapshot": {}})
    queue.cancel(parent["id"])
    child = store.job(parent["params"]["followup"]["id"])
    assert child["state"] == "cancelled"
    assert store.job(unrelated["id"])["state"] == "queued"


@pytest.mark.parametrize(
    "model,native",
    [
        ("whisper-1", {"word_timing": True, "speaker_labels": False}),
        ("gpt-4o-transcribe-diarize", {"word_timing": False, "speaker_labels": True}),
        ("gpt-4o-transcribe", {"word_timing": False, "speaker_labels": False}),
    ],
)
def test_v45_native_capabilities_cannot_be_disabled(client, prepared, model, native):
    rid, _, _ = prepared
    client.put("/api/settings", json={"model": model})
    response = client.post(
        "/api/transcriptions",
        json=request(
            rid,
            requested_features={"word_timing": False, "speaker_labels": False},
            enhancement=None,
        ),
    )
    assert response.status_code == 202, response.text
    parent = materialize_response(client, response)[0]
    assert parent["params"]["requested_features"] == native
    assert "followup" not in parent["params"]


def test_v46_cancel_reaches_owned_parent_and_enhancement_workers(client, prepared):
    rid, _, _ = prepared
    parent = materialize_response(client, client.post("/api/transcriptions", json=request(rid)))[0]
    queue, store = client.app.state.queue, client.app.state.store
    queue.process(parent["id"])
    child_id = parent["params"]["followup"]["id"]
    for worker, identifier in ((queue.workers[1], parent["id"]), (queue.workers[-1], child_id)):
        worker.active_id = identifier
        store.update_job(identifier, state="running")
    queue.cancel(parent["id"])
    assert queue.workers[1].cancel_event.is_set()
    assert queue.workers[-1].cancel_event.is_set()


def test_v45_compatible_backend_with_both_features_needs_no_enhancement(client, prepared):
    rid, _, _ = prepared
    combined = client.post(
        "/api/providers",
        json={
            "name": "Combined backend",
            "base_url": "http://127.0.0.1:9999/v1",
            "model": "org/combined",
            "capability": "words_speakers",
        },
    ).json()
    response = client.post(
        "/api/transcriptions",
        json=request(
            rid,
            provider_ids=[combined["id"]],
            enhancement=None,
        ),
    )
    assert response.status_code == 202, response.text
    parent = materialize_response(client, response)[0]
    assert parent["params"]["requested_features"] == {
        "word_timing": True,
        "speaker_labels": True,
    }
    assert "followup" not in parent["params"]


def test_v45_cloud_plan_is_frozen_and_protected_policy_checked_before_work(
    client, prepared, credentials, monkeypatch
):
    from cbs_transcriber import runtime_compute

    monkeypatch.setattr("cbs_transcriber.runtime_batches.freeze_plan", lambda *_: None)

    monkeypatch.setattr(
        runtime_compute,
        "freeze",
        lambda *_: {
            "runtime_product": runtime_compute.MIG_PRODUCT,
            "runtime_compute_policy": runtime_compute.POLICY,
        },
    )
    rid, _, calls = prepared
    store, queue = client.app.state.store, client.app.state.queue
    store.execute("UPDATE projects SET classification='protected'")
    client.put("/api/settings", json={"approved_for": ["protected"]})
    connection = providers.save_profile(
        store,
        credentials,
        "UCloud",
        providers.Settings(
            provider_type="ucloud",
            base_url="https://cloud.invalid",
            model="large-v3",
            approved_for=[],
            ucloud_transcriber={
                "project_id": "project",
                "storage_path": "/drive/cbs",
                "application": {"name": "transcriber", "version": "1.12"},
                "product": {"id": "gpu-nvidia-b200-1-gpu", "category": "gpu", "provider": "ucloud"},
            },
        ),
        api_key="synthetic-cloud",
        remember=False,
    )
    body = request(rid, enhancement={"execution": "ucloud"})
    response = client.post("/api/transcriptions", json=body)
    assert response.status_code == 403, response.text
    assert store.all("SELECT id FROM jobs") == []

    def approve(values):
        current = providers.profile(store, connection["id"])
        settings = providers.Settings.model_validate(current["settings"])
        settings.approved_for = values
        providers.save_profile(
            store, credentials, current["name"], settings, identifier=connection["id"]
        )

    approve(["protected"])
    # Requested speaker labels need the cached speaker model before any UCloud allocation,
    # even when the enhancement options themselves did not ask for speakers (B122).
    monkeypatch.setattr(enhancement_api, "runtime_stamped", lambda *a: True)
    response = client.post("/api/transcriptions", json=body)
    failed = materialize_response(client, response)[0]
    assert failed["state"] == "failed" and calls == []
    assert "followup" not in failed["params"]
    monkeypatch.setattr(enhancement_api, "speaker_model_available", lambda *a: True)
    response = client.post("/api/transcriptions", json=body)
    assert response.status_code == 202, response.text
    parent = materialize_response(client, response)[0]
    queue.process(parent["id"])
    child = store.job(parent["params"]["followup"]["id"])
    frozen = child["params"]["ucloud_api"]
    assert frozen["provider_id"] == connection["id"]
    assert frozen["runtime_id"].startswith("whisperx-3.8.6-cu128-")
    assert child["params"]["enhancement"]["device"] == "cuda"
    assert "cloud_settings" not in child["params"]
    assert "synthetic-cloud" not in str(child["params"])
    approve([])
    with pytest.raises(ValueError, match="protected"):
        queue.process(child["id"])
    assert len(calls) == 1
    assert len(store.results(rid)) == 2
    assert not store.job(child["id"])["params"].get("ucloud_run")


def test_v46_enhancement_retry_does_not_requeue_asr(client, prepared):
    rid, _, calls = prepared
    parent = materialize_response(client, client.post("/api/transcriptions", json=request(rid)))[0]
    queue, store = client.app.state.queue, client.app.state.store
    queue.process(parent["id"])
    child_id = parent["params"]["followup"]["id"]
    store.update_job(parent["id"], state="completed")
    store.update_job(child_id, state="failed", message="Synthetic enhancement failure")
    response = client.post(f"/api/jobs/{child_id}/retry", json={})
    assert response.status_code == 200, response.text
    assert store.job(child_id)["state"] == "queued"
    assert store.job(parent["id"])["state"] == "completed"
    assert len(calls) == 1
    assert len(store.all("SELECT id FROM jobs WHERE kind IN ('transcribe','enhance')")) == 2


def test_v46_cancelled_transcription_creates_no_followup_until_explicit_retry(
    client, prepared, monkeypatch
):
    rid, _, calls = prepared
    parent = materialize_response(client, client.post("/api/transcriptions", json=request(rid)))[0]
    queue, store = client.app.state.queue, client.app.state.store
    original = providers.transcribe_file

    def cancel_after_response(*args):
        result = original(*args)
        queue.cancel_event.set()
        return result

    monkeypatch.setattr(providers, "transcribe_file", cancel_after_response)
    with pytest.raises(media.Cancelled):
        queue.process(parent["id"])
    assert len(store.results(rid)) == 1
    assert not store.all("SELECT id FROM jobs WHERE kind='enhance'")
    queue.cancel_event.clear()
    queue.process(parent["id"])
    assert len(calls) == 1
    assert len(store.results(rid)) == 2
    assert len(store.all("SELECT id FROM jobs WHERE kind='enhance'")) == 1
