"""Complete captures use tiny synthetic owned libraries; no real data or remote calls."""

import hashlib
import json
import threading
from pathlib import Path

import pytest

from cbs_transcriber import portable_capture as capture
from cbs_transcriber import portable_inventory as inventory
from cbs_transcriber import portable_transport as transport


def ready_recording(client, wav_bytes):
    store = client.app.state.store
    project = store.create_project("Synthetic complete backup")
    recording = client.post(
        "/api/recordings",
        data={"project_id": project["id"]},
        files={"file": ("test.wav", wav_bytes, "audio/wav")},
    ).json()["recording"]
    store.execute("UPDATE jobs SET state='done'")
    store.execute("UPDATE recordings SET status='ready'")
    return store, recording


def test_capture_copies_originals_and_deduplicates_exact_bytes(client, wav_bytes, tmp_path):
    store, recording = ready_recording(client, wav_bytes)
    directory = store.directory(recording["id"])
    (directory / "audio.mp3").write_bytes(b"regenerable playback")
    (directory / "credentials.json").write_bytes(b"SYNTHETIC_TOKEN_CANARY")
    with store.library_lock():
        result = capture.capture_library(
            store, client.app.state.queue, tmp_path / "packages", gate=capture.CaptureGate()
        )
    root = Path(result["folder"])
    observed = transport.inspect_transport(root, expected_commit_sha256=result["commit_sha256"])
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert result["originals"] == 1 and observed["file_mapping_count"] == 1
    [original] = manifest["files"]
    assert original["role"] == "original"
    assert (root / "objects" / original["object"]).read_bytes() == wav_bytes
    assert original["object"] == hashlib.sha256(wav_bytes).hexdigest()
    assert all(
        b"SYNTHETIC_TOKEN_CANARY" not in path.read_bytes() for path in (root / "objects").iterdir()
    )
    assert (directory / "original.wav").read_bytes() == wav_bytes


def test_capture_refuses_missing_original_and_never_publishes(client, wav_bytes, tmp_path):
    store, recording = ready_recording(client, wav_bytes)
    (store.directory(recording["id"]) / "original.wav").unlink()
    parent = tmp_path / "packages"
    with store.library_lock(), pytest.raises(ValueError, match="every original"):
        capture.capture_library(store, client.app.state.queue, parent, gate=capture.CaptureGate())
    assert not list(parent.glob("portable-*"))


def test_capture_refuses_active_queue_and_requires_same_store_lease(client, tmp_path):
    store = client.app.state.store
    with pytest.raises(RuntimeError, match="serving library lease"):
        capture.capture_library(
            store, client.app.state.queue, tmp_path / "packages", gate=capture.CaptureGate()
        )


def test_capture_gate_waits_for_mutation_and_reopens_after_failure():
    gate = capture.CaptureGate()
    gate.enter_mutation()
    entered, leave = threading.Event(), threading.Event()

    def writer():
        with gate.capture():
            entered.set()
            leave.wait(5)

    worker = threading.Thread(target=writer)
    worker.start()
    assert not entered.wait(0.05)
    gate.leave_mutation()
    assert entered.wait(2)
    leave.set()
    worker.join(2)
    assert not worker.is_alive()
    with pytest.raises(ValueError), gate.capture():
        raise ValueError("synthetic failure")
    gate.enter_mutation()
    gate.leave_mutation()


@pytest.mark.parametrize(
    "name",
    ["token.txt", "enhancements/" + "a" * 32 + "/unknown-evidence.json", "sources/arbitrary.json"],
)
def test_unknown_artifact_names_fail_closed(name):
    with pytest.raises(ValueError, match="unsupported"):
        inventory.recording_role(name, original="original.wav", job_ids={"a" * 32})


def test_broken_link_parent_is_rejected_before_creating_destination(tmp_path):
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(tmp_path / "missing-outside", target_is_directory=True)
    except OSError:
        pytest.skip("Creating Windows filesystem links requires host permission.")
    with pytest.raises(ValueError, match="filesystem links"):
        capture._parents(linked / "new-package")
    assert not (tmp_path / "missing-outside").exists()
