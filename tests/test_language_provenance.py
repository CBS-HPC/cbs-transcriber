"""Mocked native output keeps a frozen language choice across retry and restore."""

import tempfile
from pathlib import Path

import httpx
from admission_support import inspect_and_materialize
from test_ucloud_native import REFERENCE, native_selection
from test_ucloud_native import native_lease as native_lease
from test_ucloud_native import remote as remote

from cbs_transcriber import language_preferences, portable_capture, portable_restore
from cbs_transcriber import ucloud_native as native
from cbs_transcriber.store import Store


def test_result_job_keeps_recording_scope_through_retry_and_portable_restore(
    client, credentials, wav_bytes, native_lease, remote, tmp_path, monkeypatch
):
    store = client.app.state.store
    original = remote.handle

    def handle(request):
        if request.url.path == "/api/hpc/apps/byNameAndVersion":
            return httpx.Response(200, json=remote.template["status"]["resolvedApplication"])
        return original(request)

    remote.handle = handle

    def discover(settings, _key, *, compute_id=None):
        assert compute_id == native.MIG_PRODUCT["id"]
        return {"ready": True, "setup": settings.ucloud_transcriber.model_dump()}

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", discover)
    provider, recording = native_selection(
        client,
        wav_bytes,
        setup={"template_job_id": "", "application": REFERENCE, "product": native.MIG_PRODUCT},
    )
    language_preferences.save(store, "project", recording["project_id"], "da", 0)
    language_preferences.save(store, "recording", recording["id"], "da", 0)
    accepted = client.post(
        "/api/transcriptions",
        json={
            "provider_ids": [provider["id"]],
            "recording_ids": [recording["id"]],
        },
    )
    assert accepted.status_code == 202, accepted.text
    job = inspect_and_materialize(store, credentials, [accepted.json()[0]["id"]])[0]
    expected = {"scope": "recording", "language": "da"}
    assert job["params"]["input_acceptance"]["language_preference"] == expected
    client.app.state.queue.process(job["id"])
    client.app.state.queue.process(job["id"])
    [listed] = store.results(recording["id"])
    result = store.result(recording["id"], listed["id"])
    assert result["job_id"] == job["id"]
    assert len(remote.submissions) == 1
    assert remote.submissions[0]["product"] == native.MIG_PRODUCT
    assert (
        store.job(result["job_id"])["params"]["input_acceptance"]["language_preference"] == expected
    )
    store.update_job(job["id"], state="completed")
    saved = portable_capture.capture_library(
        store, client.app.state.queue, tmp_path / "packages", gate=portable_capture.CaptureGate()
    )
    with tempfile.TemporaryDirectory(prefix="cbs-lang-", dir=Path.home()) as short_root:
        root = Path(short_root).resolve()
        assert root.parent == Path.home().resolve()
        target = root / "restored"
        portable_restore.restore_library(
            saved["folder"], expected_commit_sha256=saved["commit_sha256"], target=target
        )
        restored = Store(target)
        [listed] = restored.results(recording["id"])
        restored_result = restored.result(recording["id"], listed["id"])
        assert restored_result["job_id"] == job["id"]
        assert (
            restored.job(job["id"])["params"]["input_acceptance"]["language_preference"] == expected
        )
