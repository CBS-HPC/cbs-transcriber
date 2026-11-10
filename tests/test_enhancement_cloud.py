"""UCloud enhancement control/transport are mocked; no allocations or private uploads."""

import copy
import json
import shlex
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from cbs_transcriber import enhancement_cloud as cloud
from cbs_transcriber import enhancement_remote as remote
from cbs_transcriber.enhancement import EnhancementOptions
from cbs_transcriber.enhancement_worker import write_json
from cbs_transcriber.media import Cancelled
from cbs_transcriber.transcripts import normalize_segments

SECRET = "synthetic-ucloud-control-secret"
PRODUCT = {"id": "gpu-template", "category": "gpu", "provider": "ucloud"}
REFERENCE = {"name": "python-runtime", "version": "1"}
TEMPLATE = {
    "specification": {
        "application": REFERENCE,
        "product": PRODUCT,
        "replicas": 1,
        "parameters": {},
        "resources": [{"type": "file", "path": "/drive/cache"}],
    },
    "status": {"resolvedApplication": {"metadata": REFERENCE}},
}


class MockCloud:
    def __init__(self, store, job):
        self.store, self.job = store, job
        self.submissions, self.stops, self.transfers = [], [], []
        self.submission_error = None
        self.hook = lambda *a: None
        self.bad_output = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def retrieve_job(self, identifier, **kwargs):
        if identifier == "template":
            return copy.deepcopy(TEMPLATE)
        assert identifier == "owned-enhancement"
        return {
            "status": {"state": "RUNNING"},
            "updates": [{"status": "ssh researcher@ssh.cloud.example -p 2222"}],
        }

    def submit_job(self, spec):
        assert self.store.job(self.job["id"])["params"]["ucloud_run"]["submission_intent"]
        self.submissions.append(copy.deepcopy(spec))
        if self.submission_error:
            raise self.submission_error
        return {"id": "owned-enhancement"}

    def terminate_job(self, identifier):
        self.stops.append(identifier)

    def transport(self, args, cancel, *, output=None, **kwargs):
        assert SECRET not in str(args)
        self.transfers.append(args)
        self.hook(args, cancel)
        if output:
            command = shlex.split(args[-1])
            if "--doctor" in command:
                data = {"ready": True, "version": "3.8.6"}
            elif "status" in command:
                data = {"state": "complete", "stage": "complete"}
            else:
                assert "read-stage" in command and command[-1] in {"alignment", "diarization"}
                data = (
                    {
                        "turns": [
                            {"start": 0, "end": 1, "speaker": "A"},
                            {"start": 1, "end": 3, "speaker": "B"},
                        ]
                    }
                    if command[-1] == "diarization"
                    else {
                        "passages": [
                            {
                                "id": "0",
                                "words": [
                                    {
                                        "word": "Changed" if self.bad_output else "Hej",
                                        "start": 0,
                                        "end": 1,
                                        "score": 0.9,
                                    },
                                    {"word": "København.", "start": 1, "end": 3, "score": 0.9},
                                ],
                            }
                        ]
                    }
                )
            output.write_text(json.dumps(data), encoding="utf-8")


def prepared(client, monkeypatch, **options):
    store = client.app.state.store
    saved = client.put(
        "/api/enhancement-cloud-settings",
        json={
            "project_id": "cloud-project",
            "template_job_id": "template",
            "api_token": SECRET,
        },
    )
    assert saved.status_code == 200, saved.text

    project = store.create_project("Protected enhancement", "protected")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Hej København.", "start": 0, "end": 3}]),
        {},
        "Reviewed",
        {},
    )
    # These are frozen pre-API/SSH jobs, not new whole-recording admission requests.
    settings = cloud.load_cloud_settings(store)
    cloud.require_setup(settings, client.app.state.credentials)
    cloud.require_approval(store, project["id"], settings)
    job = store.enqueue_enhancement(
        recording["id"],
        result["id"],
        1,
        {
            "enhancement": EnhancementOptions(execution="ucloud", **options).model_dump(
                exclude={"timing_policy"}
            ),
            "cloud_settings": settings.model_dump(),
        },
    )
    assert "audio_input" not in job["params"]
    assert "timing_policy" not in job["params"]["enhancement"]
    backend = MockCloud(store, job)
    monkeypatch.setattr(cloud, "client_for", lambda *a: backend)
    monkeypatch.setattr(cloud, "transport", backend.transport)
    monkeypatch.setattr(
        "cbs_transcriber.enhancement.media.ffmpeg",
        lambda args, *a: Path(args[-1]).write_bytes(b"synthetic audio"),
    )
    return backend, job, recording, result


def test_v34_ucloud_enhancement_preserves_template_source_and_appends_once(client, monkeypatch):
    backend, job, recording, original = prepared(client, monkeypatch)
    queue = client.app.state.queue
    queue.process(job["id"])
    queue.process(job["id"])
    assert len(backend.submissions) == 1 and backend.stops == ["owned-enhancement"]
    assert backend.submissions[0]["product"] == PRODUCT
    assert backend.submissions[0]["resources"] == TEMPLATE["specification"]["resources"]
    assert SECRET not in json.dumps(backend.submissions)
    current = queue.store.recording(recording["id"])
    assert len(current["transcripts"]) == 2 and current["active_transcript_id"] == original["id"]
    enhanced = next(r for r in current["transcripts"] if r["id"] != original["id"])
    assert enhanced["source"]["execution"] == "ucloud"
    assert queue.store.result(recording["id"], enhanced["id"])["segments"][0]["word_timing"]
    projected = client.get("/api/jobs").json()[0]
    assert projected["ucloud_job_id"] == "owned-enhancement"
    assert projected["ucloud_stop_requested"] and SECRET not in json.dumps(projected)
    assert "Hej" not in json.dumps(projected)
    folder = queue.store.directory(recording["id"]) / "enhancements" / job["id"]
    request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
    assert set(request["passages"][0]) == {"id", "text", "start", "end"}


@pytest.mark.parametrize("align", [True, False])
def test_v34_cloud_speaker_stage_preserves_words_and_omits_unneeded_text(
    client, monkeypatch, align
):
    _, job, recording, original = prepared(client, monkeypatch, align=align, diarize=True)
    queue = client.app.state.queue
    queue.process(job["id"])
    result = next(
        r
        for r in queue.store.recording(recording["id"])["transcripts"]
        if r["id"] != original["id"]
    )
    segments = queue.store.result(recording["id"], result["id"])["segments"]
    assert "".join(s["text"] for s in segments) == "Hej København."
    folder = queue.store.directory(recording["id"]) / "enhancements" / job["id"]
    request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
    if not align:
        assert request["passages"] == [] and "Hej" not in (folder / "request.json").read_text()


def test_v34_uncertain_allocation_is_not_resubmitted(client, monkeypatch):
    backend, job, _, _ = prepared(client, monkeypatch)
    backend.submission_error = httpx.ReadTimeout(SECRET)
    with pytest.raises(ValueError, match="connection failed") as error:
        client.app.state.queue.process(job["id"])
    assert SECRET not in str(error.value)
    with pytest.raises(ValueError, match="uncertain"):
        client.app.state.queue.process(job["id"])
    assert len(backend.submissions) == 1 and not backend.transfers


def test_v34_uncertain_retry_preserves_frozen_request_while_a_reader_has_it_open(
    client, monkeypatch
):
    backend, job, recording, _ = prepared(client, monkeypatch)
    backend.submission_error = httpx.ReadTimeout(SECRET)
    queue = client.app.state.queue
    with pytest.raises(ValueError, match="connection failed"):
        queue.process(job["id"])
    folder = queue.store.directory(recording["id"]) / "enhancements" / job["id"]
    request = folder / "request.json"
    before = request.read_bytes()
    # Windows readers may share read/write without permitting deletion of their open file.
    # A retry has the same frozen input and must not need to replace that file.
    with request.open("rb") as reader:
        with pytest.raises(ValueError, match="uncertain"):
            queue.process(job["id"])
        assert reader.read() == before
    assert request.read_bytes() == before
    assert not (folder / "request.pending").exists()
    assert len(backend.submissions) == 1 and not backend.transfers


def test_v34_json_checkpoint_unchanged_bytes_skip_replacement_but_changes_remain_atomic(
    tmp_path, monkeypatch
):
    path = tmp_path / "request.json"
    original = {"text": "Frozen æøå", "offset": 0}
    write_json(path, original)
    before = path.read_bytes()
    calls = []
    replace = Path.replace

    def reject_replace(pending, target):
        calls.append((pending, target))
        raise PermissionError("synthetic locked target")

    monkeypatch.setattr(Path, "replace", reject_replace)
    write_json(path, original)
    assert calls == [] and not path.with_suffix(".pending").exists()
    changed = {"text": "Changed æøå", "offset": 0}
    with pytest.raises(PermissionError, match="locked target"):
        write_json(path, changed)
    assert len(calls) == 1 and path.read_bytes() == before
    assert json.loads(path.with_suffix(".pending").read_text(encoding="utf-8")) == changed
    monkeypatch.setattr(Path, "replace", replace)
    write_json(path, changed)
    assert json.loads(path.read_text(encoding="utf-8")) == changed
    assert not path.with_suffix(".pending").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows open readers deny file replacement")
def test_v71_changed_json_preserves_old_file_until_windows_reader_releases_it(tmp_path):
    path = tmp_path / "request.json"
    original, changed = {"text": "Frozen æøå"}, {"text": "Changed æøå"}
    write_json(path, original)
    before = path.read_bytes()
    with path.open("rb") as reader:
        with pytest.raises(PermissionError):
            write_json(path, changed)
        assert path.read_bytes() == reader.read() == before
        assert json.loads(path.with_suffix(".pending").read_text(encoding="utf-8")) == changed
    write_json(path, changed)
    assert json.loads(path.read_text(encoding="utf-8")) == changed
    assert not path.with_suffix(".pending").exists()


def test_v34_revocation_blocks_next_private_upload_but_allows_owned_stop(client, monkeypatch):
    backend, job, _, _ = prepared(client, monkeypatch)

    def revoke(args, cancel):
        if args[0] == "scp" and args[-1].endswith("request.json.pending"):
            settings = cloud.load_cloud_settings(backend.store)
            settings.approved_for_protected = False
            cloud.save_cloud_settings(backend.store, settings)

    backend.hook = revoke
    with pytest.raises(ValueError, match="Protected"):
        client.app.state.queue.process(job["id"])
    assert not any(a[-1].endswith("audio.wav.pending") for a in backend.transfers)
    backend.store.update_job(job["id"], state="failed")
    assert client.post(f"/api/jobs/{job['id']}/retry", json={}).status_code == 400
    assert client.post(f"/api/jobs/{job['id']}/cancel", json={}).status_code == 200
    assert backend.stops == ["owned-enhancement"]
    assert backend.store.job(job["id"])["state"] == "cancelled"


def test_v34_changed_frozen_cloud_identity_blocks_protected_execution(client, monkeypatch):
    backend, job, _, _ = prepared(client, monkeypatch)
    settings = cloud.load_cloud_settings(backend.store)
    settings.template_job_id = "different-template"
    cloud.save_cloud_settings(backend.store, settings)
    with pytest.raises(ValueError, match="Protected"):
        client.app.state.queue.process(job["id"])
    assert not backend.submissions and not backend.transfers


@pytest.mark.parametrize("shutdown", [True, False])
def test_v34_shutdown_retains_remote_job_explicit_cancel_stops_owned_only(
    client, monkeypatch, shutdown
):
    backend, job, _, _ = prepared(client, monkeypatch)
    queue = client.app.state.queue
    if shutdown:
        queue.stopping.set()

    def interrupt(args, cancel):
        if args[0] == "scp" and args[-1].endswith("request.json.pending"):
            cancel.set()

    backend.hook = interrupt
    with pytest.raises(Cancelled):
        queue.process(job["id"])
    assert backend.stops == ([] if shutdown else ["owned-enhancement"])
    if shutdown:
        queue.stopping.clear()
        queue.cancel_event.clear()
        backend.hook = lambda *a: None
        queue.process(job["id"])
        assert len(backend.submissions) == 1 and backend.stops == ["owned-enhancement"]


def test_v34_invalid_cloud_output_preserves_remote_and_reviewed_result(client, monkeypatch):
    backend, job, recording, original = prepared(client, monkeypatch)
    backend.bad_output = True
    queue = client.app.state.queue
    with pytest.raises(ValueError, match="wording"):
        queue.process(job["id"])
    assert backend.stops == []
    folder = queue.store.directory(recording["id"]) / "enhancements" / job["id"]
    assert (folder / "alignment.invalid.json").exists() and not (folder / "alignment.json").exists()
    assert queue.store.recording(recording["id"])["active_transcript_id"] == original["id"]
    backend.bad_output = False
    queue.process(job["id"])
    assert len(backend.submissions) == 1 and backend.stops == ["owned-enhancement"]


def test_v34_cloud_metadata_check_redacts_tokens_and_preserves_explicit_denial(client, monkeypatch):
    backend, _, _, _ = prepared(client, monkeypatch)
    response = client.post("/api/enhancement-cloud/check", json={})
    assert response.status_code == 200 and "No job was started" in response.json()["note"]
    assert not backend.submissions and not backend.transfers
    assert SECRET not in response.text
    assert (
        client.put(
            "/api/enhancement-cloud-settings", json={"approved_for_protected": False}
        ).status_code
        == 200
    )
    response = client.put("/api/enhancement-cloud-settings", json={"project_id": "new-project"})
    assert not response.json()["approved_for_protected"]
    assert SECRET not in response.text and response.json()["has_api_token"]
    assert SECRET not in backend.store.one("SELECT value FROM settings WHERE id=4")["value"]


def test_v34_cloud_requires_cache_and_safe_paths():
    with pytest.raises(ValueError):
        cloud.CloudEnhancementSettings(work_root="/work/../private")
    with pytest.raises(ValueError):
        cloud.CloudEnhancementSettings(remote_python="/python;cat")
    with pytest.raises(ValueError):
        cloud.endpoint("ssh user@host;bad -p 22")
    assert cloud.endpoint("ssh user@ssh.example -p 2222") == ("user@ssh.example", "2222")
    with pytest.raises(ValueError, match="cached"):
        from cbs_transcriber.enhancement import validate_input

        validate_input(
            {"segments": []}, EnhancementOptions(execution="ucloud", allow_downloads=True), 3
        )


def test_v34_cleanup_retry_uses_saved_output_without_reallocating(client, monkeypatch):
    backend, job, recording, _ = prepared(client, monkeypatch)
    stop = backend.terminate_job

    def unavailable(identifier):
        raise httpx.ReadTimeout(SECRET)

    backend.terminate_job = unavailable
    with pytest.raises(ValueError, match="connection failed"):
        client.app.state.queue.process(job["id"])
    backend.terminate_job = stop
    client.app.state.queue.process(job["id"])
    assert len(backend.submissions) == 1 and backend.stops == ["owned-enhancement"]
    assert len(backend.store.recording(recording["id"])["transcripts"]) == 2


def test_v34_transport_curates_errors_and_bounds_captured_output(tmp_path, capfd):
    with pytest.raises(ValueError, match="SSH transport failed") as failure:
        cloud.transport(
            [sys.executable, "-c", "import sys;sys.stderr.write('synthetic-secret');sys.exit(1)"],
            threading.Event(),
            timeout=5,
        )
    assert "synthetic-secret" not in str(failure.value)
    captured = capfd.readouterr()
    assert "synthetic-secret" not in captured.out + captured.err
    with pytest.raises(ValueError, match="oversized"):
        cloud.transport(
            [sys.executable, "-c", "print('x'*100000)"],
            threading.Event(),
            timeout=5,
            output=tmp_path / "large.pending",
            limit=100,
        )


def test_v34_remote_lock_and_environment_do_not_duplicate_or_expose_secrets(tmp_path, monkeypatch):
    locks, calls = [], []
    fake = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *a: locks.append(a))
    monkeypatch.setitem(sys.modules, "fcntl", fake)
    monkeypatch.setenv("HF_TOKEN", SECRET)
    monkeypatch.setenv("UCLOUD_TOKEN", SECRET)
    monkeypatch.setattr(
        remote.subprocess, "run", lambda *a, **kw: calls.append(kw) or SimpleNamespace(returncode=0)
    )
    remote.run(tmp_path, "generation")
    assert len(locks) == len(calls) == 1
    assert len(calls[0]["pass_fds"]) == 1
    assert SECRET not in json.dumps(calls[0]["env"])
    assert remote.status(tmp_path)["state"] == "complete"

    def locked(*args):
        raise BlockingIOError()

    fake.flock = locked
    remote.run(tmp_path, "second-attempt")
    assert len(calls) == 1 and remote.status(tmp_path)["generation"] == "generation"
    remote.write_status(tmp_path, "running", "orphaned-wrapper")
    (tmp_path / "alignment.json").write_text("{}", encoding="utf-8")
    assert remote.status(tmp_path, "alignment")["state"] == "complete"
    (tmp_path / "alignment.json").unlink()
    fake.flock = lambda *a: None
    assert remote.status(tmp_path, "alignment")["state"] == "failed"
