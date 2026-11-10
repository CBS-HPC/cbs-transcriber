"""Standing consent reaches the actual frozen UCloud re-transcription runner."""

from types import SimpleNamespace

import pytest
from test_input_admission import native_provider
from test_retranscription import request
from test_retranscription import reviewed as reviewed

from cbs_transcriber import enhancement_api, runtime_compute
from cbs_transcriber.enhancement import EnhancementOptions


@pytest.mark.parametrize(
    ("standing", "confirmed", "ready", "status", "prepare"),
    [
        (None, False, False, 202, True),
        (True, False, False, 202, True),
        (False, False, False, 409, None),
        (False, True, False, 202, True),
        (True, False, True, 202, False),
        (False, False, True, 202, False),
    ],
)
def test_retranscription_freezes_final_preparation_consent_for_actual_runner(
    reviewed, credentials, monkeypatch, tmp_path, standing, confirmed, ready, status, prepare
):
    client, store, recording_id, result = reviewed
    native_provider(store, credentials)
    if standing is not None:
        response = client.put("/api/enhancement-settings", json={"prepare_ucloud_models": standing})
        assert response.status_code == 200, response.text
    monkeypatch.setattr(enhancement_api, "whisperx_ready", lambda *args: ready)
    monkeypatch.setattr(
        runtime_compute,
        "freeze",
        lambda *args: {
            "runtime_product": runtime_compute.MIG_PRODUCT,
            "runtime_compute_policy": runtime_compute.POLICY,
        },
    )
    monkeypatch.setattr("cbs_transcriber.runtime_batches.freeze_plan", lambda *args: None)
    monkeypatch.setattr(enhancement_api.native, "client_for", pytest.fail)
    response = request(
        client, recording_id, result, ["1"], execution="ucloud", prepare_runtime=confirmed
    )
    assert response.status_code == status, response.text
    if status == 409:
        assert response.json()["detail"]["code"] == "prepare_runtime"
        assert store.all("SELECT id FROM jobs") == []
        return
    job = store.job(response.json()["id"])
    options = EnhancementOptions.model_validate(job["params"]["enhancement"])
    # Exercise real freezing and Run.__init__: the supervisor consumes the ucloud_api flag,
    # so asserting only the parallel EnhancementOptions field misses a stale frozen flag.
    runner = enhancement_api.Run(
        SimpleNamespace(store=store),
        job,
        store.recording(recording_id),
        tmp_path,
        options,
        lambda *args: None,
    )
    assert runner.prepare is prepare
    assert job["params"]["ucloud_api"]["prepare_runtime"] is prepare
    assert options.prepare_runtime is prepare


def test_retranscription_ucloud_still_refuses_per_run_downloads(reviewed, monkeypatch):
    client, store, recording_id, result = reviewed
    monkeypatch.setattr(enhancement_api, "connection", pytest.fail)
    response = request(
        client, recording_id, result, ["1"], execution="ucloud", allow_downloads=True
    )
    assert response.status_code == 400, response.text
    assert "prepared runtime's cached models" in response.text
    assert store.all("SELECT id FROM jobs") == []
