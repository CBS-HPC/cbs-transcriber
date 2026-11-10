"""Native control/transfer protocol checks; no network, credentials, or allocations."""

import copy
import json
import struct
import threading
from contextlib import ExitStack

import httpx
import pytest
from admission_support import inspect_and_materialize

from cbs_transcriber import ucloud_native as native
from cbs_transcriber.media import Cancelled
from cbs_transcriber.providers import Settings, credential_scope
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments

REAL_CLIENT = httpx.Client
SECRET = "synthetic-control-secret"
PRODUCT = {"id": "gpu-nvidia-b200-1-gpu", "category": "gpu", "provider": "ucloud"}
SETUP = {
    "project_id": "project",
    "template_job_id": "template",
    "storage_path": "/drive/cbs",
    "cleanup_after_success": False,
    "product": PRODUCT,
}
REFERENCE = {"name": "transcriber-batch", "version": "1.12"}
TEMPLATE = {
    "specification": {
        "application": REFERENCE,
        "product": PRODUCT,
        "replicas": 1,
        "parameters": {"language": {"type": "text", "value": "da"}},
        "resources": [{"type": "file", "path": "/drive/existing", "readOnly": True}],
    },
    "status": {
        "resolvedApplication": {
            "metadata": REFERENCE,
            "invocation": {
                "applicationType": "BATCH",
                "parameters": [
                    {"name": "input_file", "type": "input_file"},
                    {"name": "output_dir", "type": "input_directory"},
                    {"name": "model", "type": "enumeration", "options": [{"value": "large-v3"}]},
                    {"name": "language", "type": "text"},
                    {
                        "name": "output_format",
                        "type": "enumeration",
                        "options": [{"value": "json"}],
                    },
                ],
            },
        }
    },
}
PAYLOAD = {
    "segments": [
        {
            "text": "Hej CBS.",
            "start": 0,
            "end": 1,
            "speaker": "SPEAKER_00",
            "words": [
                {"word": "Hej", "start": 0, "end": 0.4},
                {"word": "CBS.", "start": 0.4, "end": 1},
            ],
        }
    ]
}


@pytest.fixture
def remote(monkeypatch):
    class Remote:
        template = copy.deepcopy(TEMPLATE)
        calls = []
        submissions = []
        uploaded = bytearray()
        states = ["SUCCESS"]
        unknown_submission = False
        protocol = "CHUNKED"
        error_status = None

        def handle(self, request):
            self.calls.append(request)
            if request.url.host == "files.invalid":
                assert "authorization" not in request.headers
                assert SECRET not in str(request.headers)
                if request.url.path == "/upload":
                    assert int(request.headers["Chunked-Upload-Offset"]) == len(self.uploaded)
                    assert request.headers["Chunked-Upload-Token"] == "transfer-token"
                    self.uploaded.extend(request.content)
                    return httpx.Response(200)
                return httpx.Response(200, json=PAYLOAD)
            assert request.url.host == "cloud.invalid"
            assert request.headers["Authorization"] == "Bearer " + SECRET
            assert request.headers["Project"] == "project"
            if self.error_status:
                return httpx.Response(self.error_status, text=SECRET)
            path = request.url.path
            if path == "/api/hpc/apps/byNameAndVersion":
                return httpx.Response(200, json=self.template["status"]["resolvedApplication"])
            if path == "/api/jobs/retrieve":
                if request.url.params["id"] == "template":
                    return httpx.Response(200, json=self.template)
                state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
                return httpx.Response(200, json={"status": {"state": state}})
            if path == "/api/files/retrieve":
                return httpx.Response(
                    200,
                    json={
                        "id": request.url.params["id"],
                        "permissions": {"myself": ["EDIT"]},
                        "status": {
                            "type": "DIRECTORY",
                            "sizeInBytes": len(self.uploaded),
                        },
                    },
                )
            if path == "/api/files/folder":
                return httpx.Response(200, json={})
            if path == "/api/files/upload":
                return httpx.Response(
                    200,
                    json={
                        "responses": [
                            {
                                "endpoint": "https://files.invalid/upload",
                                "protocol": self.protocol,
                                "token": "transfer-token",
                            }
                        ]
                    },
                )
            if path == "/api/jobs":
                self.submissions.append(json.loads(request.content)["items"][0])
                if self.unknown_submission:
                    raise httpx.ReadTimeout("private upstream message", request=request)
                return httpx.Response(200, json={"responses": [{"id": "remote-job"}]})
            if path == "/api/jobs/terminate":
                assert json.loads(request.content) == {"items": [{"id": "remote-job"}]}
                return httpx.Response(200, json={})
            if path == "/api/files/browse":
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "id": request.url.params["path"] + "/recording.json",
                            }
                        ]
                    },
                )
            if path == "/api/files/download":
                return httpx.Response(
                    200,
                    json={
                        "responses": [
                            {
                                "endpoint": "https://files.invalid/download?transfer=token",
                            }
                        ]
                    },
                )
            raise AssertionError("Unexpected mocked request: " + path)

    result = Remote()
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: REAL_CLIENT(
            *a,
            **kw,
            transport=httpx.MockTransport(result.handle),
        ),
    )
    return result


@pytest.fixture
def native_lease(client):
    """Keep the same Store lease through explicit admission and native execution."""
    with ExitStack() as lease:
        lease.enter_context(client.app.state.store.library_lock())
        yield lease


def fresh_native_setup(remote, monkeypatch):
    """A saved full-GPU connection admits fresh MIG jobs; templates stay legacy."""
    remote.template["specification"]["application"] = dict(native.MIG_APPLICATION)
    remote.template["specification"]["product"] = dict(native.MIG_PRODUCT)
    remote.template["status"]["resolvedApplication"]["metadata"] = dict(native.MIG_APPLICATION)
    parameters = remote.template["status"]["resolvedApplication"]["invocation"]["parameters"]
    next(p for p in parameters if p["name"] == "language")["defaultValue"] = "da"

    def discover(settings, key, *, compute_id=None):
        assert key == SECRET and compute_id == native.MIG_PRODUCT["id"]
        assert settings.ucloud_transcriber.product == native.MIG_PRODUCT
        assert not settings.ucloud_transcriber.template_job_id
        return {"ready": True, "setup": settings.ucloud_transcriber.model_dump()}

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", discover)
    return {
        **SETUP,
        "template_job_id": "",
        "application": dict(native.MIG_APPLICATION),
        "product": dict(PRODUCT),
    }


def native_selection(client, wav_bytes, setup=None):
    """Create an immutable real original before either fresh or historical job setup."""
    store = client.app.state.store
    assert store._lease_depth > 0
    response = client.post(
        "/api/providers",
        json={
            "name": "UCloud native",
            "provider_type": "ucloud",
            "base_url": "https://cloud.invalid",
            "model": "large-v3",
            "ucloud_transcriber": {**SETUP, **(setup or {})},
            "api_key": SECRET,
            "remember": False,
        },
    )
    assert response.status_code == 201, response.text
    provider = response.json()
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "test.v2.wav")
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True)
    (directory / "original.wav").write_bytes(wav_bytes)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    return provider, recording


def frozen_native_job(client, provider, recording, setup=None):
    """Seed an explicitly historical native snapshot without new admission receipts."""
    store = client.app.state.store
    assert store._lease_depth > 0
    settings = Settings.model_validate(provider).model_dump()
    settings["ucloud_transcriber"].update(setup or {})
    job = store.add_job(
        recording["id"],
        "transcribe",
        {
            "settings": settings,
            "provider_id": provider["id"],
            "provider_name": provider["name"],
            "revision": store.recording(recording["id"])["revision"],
        },
    )
    assert not {"input_intent", "input_acceptance", "audio_input"}.intersection(job["params"])
    return job


@pytest.fixture
def native_job(client, credentials, wav_bytes, request, native_lease, remote, monkeypatch):
    setup = {**fresh_native_setup(remote, monkeypatch), **getattr(request, "param", {})}
    provider, recording = native_selection(client, wav_bytes, setup)
    store = client.app.state.store
    response = client.post(
        "/api/transcriptions",
        json={
            "provider_ids": [provider["id"]],
            "recording_ids": [recording["id"]],
        },
    )
    assert response.status_code == 202, response.text
    accepted = response.json()[0]
    assert accepted["params"]["input_intent"]["phase"] == "waiting"
    assert store.claim_job("transcribe") is None
    job = inspect_and_materialize(store, credentials, [accepted["id"]])[0]
    assert "input_acceptance" in job["params"] and "input_intent" not in job["params"]
    assert job["params"]["native_allocation"]["product"] == native.MIG_PRODUCT
    return provider, recording, job


@pytest.fixture
def legacy_native_job(client, wav_bytes, request, native_lease, remote):
    provider, recording = native_selection(client, wav_bytes)
    job = frozen_native_job(client, provider, recording, getattr(request, "param", {}))
    return provider, recording, job


def test_v26_readonly_setup_keeps_token_out_of_storage(client, credentials, remote, native_job):
    provider, _, _ = native_job
    response = client.post("/api/providers/check-ucloud", json=provider)
    assert response.status_code == 200 and response.json()["ready"]
    assert all(r.method == "GET" for r in remote.calls)
    assert not remote.submissions and not remote.uploaded
    assert SECRET not in client.get("/api/providers").text
    assert SECRET not in client.get("/api/jobs").text
    assert SECRET.encode() not in client.app.state.store.db_path.read_bytes()
    assert credentials.get("ucloud-control:https://cloud.invalid") == SECRET
    assert credential_scope(Settings.model_validate(provider)) != provider["base_url"]


@pytest.mark.parametrize("product", [None, {**PRODUCT, "id": "other-gpu"}])
def test_v76_invalid_saved_connection_blocks_new_admission(client, native_job, product):
    provider, recording, job = native_job
    store = client.app.state.store
    store.update_job(job["id"], state="cancelled")
    before = store.all("SELECT * FROM jobs")
    provider["ucloud_transcriber"]["product"] = product
    response = client.put("/api/providers/" + provider["id"], json=provider)
    assert response.status_code == 200
    response = client.post(
        "/api/transcriptions",
        json={"provider_ids": [provider["id"]], "recording_ids": [recording["id"]]},
    )
    assert response.status_code == 400
    issue = "discover a project" if product is None else PRODUCT["id"]
    assert issue in response.json()["detail"]
    assert store.all("SELECT * FROM jobs") == before


@pytest.mark.parametrize("legacy_native_job", [{"product": None}], indirect=True)
def test_v76_old_frozen_native_run_keeps_its_recorded_machine(client, remote, legacy_native_job):
    _, _, job = legacy_native_job
    store = client.app.state.store
    params = job["params"]
    legacy = {**PRODUCT, "id": "legacy-gpu"}
    remote.template["specification"]["product"] = legacy
    client.app.state.queue.process(job["id"])
    assert remote.submissions[0]["product"] == legacy
    assert store.job(job["id"])["params"]["settings"] == params["settings"]


def test_v76_setup_check_does_not_approve_another_template_machine(remote):
    remote.template["specification"]["product"] = {**PRODUCT, "id": "other-gpu"}
    configured = Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="large-v3",
        ucloud_transcriber={**SETUP, "product": None},
    )
    checked = native.check_setup(configured, SECRET)
    assert not checked["ready"]
    assert PRODUCT["id"] in checked["issue"]
    assert not remote.submissions and not remote.uploaded


def test_v76_template_machine_mismatch_fails_before_private_upload(
    client, remote, legacy_native_job
):
    _, _, job = legacy_native_job
    remote.template["specification"]["product"] = {**PRODUCT, "id": "other-gpu"}
    with pytest.raises(ValueError, match="configured B200 allocation"):
        client.app.state.queue.process(job["id"])
    assert not remote.submissions and not remote.uploaded


def test_v26_native_run_preserves_template_and_reviewed_result(
    client, remote, legacy_native_job, wav_bytes
):
    _, recording, job = legacy_native_job
    store = client.app.state.store
    original = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Reviewed"}]),
        {},
        "Reviewed",
        {"kind": "import"},
    )
    client.app.state.queue.process(job["id"])
    assert remote.uploaded == wav_bytes
    assert len(remote.submissions) == 1
    spec = remote.submissions[0]
    assert spec["product"] == PRODUCT and spec["application"] == REFERENCE
    assert spec["resources"] == TEMPLATE["specification"]["resources"]
    assert spec["parameters"]["language"]["value"] == "da"
    assert spec["parameters"]["input_file"]["path"].endswith("/recording.wav")
    assert spec["sshEnabled"] is False
    results = store.results(recording["id"])
    assert len(results) == 2
    assert store.recording(recording["id"])["active_transcript_id"] == original["id"]
    result = store.result(recording["id"], results[-1]["id"])
    assert result["segments"][0]["word_timing"]
    assert result["segments"][0]["speaker"] == "SPEAKER_00"
    client.app.state.queue.process(job["id"])
    assert len(remote.submissions) == 1 and len(store.results(recording["id"])) == 2
    info = client.get("/api/jobs").json()[0]
    assert info["ucloud_job_id"] == "remote-job"
    assert "transfer-token" not in json.dumps(store.job(job["id"]))


@pytest.mark.parametrize(
    "legacy_native_job",
    [
        {
            "input_parameter": "inputFile",
            "output_parameter": "outputDir",
            "format_parameter": "outputFormat",
        }
    ],
    indirect=True,
)
@pytest.mark.parametrize("extra", [None, "initScript", "inputDir"])
def test_observed_batch_flavor_runs_unattended_and_rejects_extra_inputs(
    client, remote, legacy_native_job, current_transcriber_application, wav_bytes, extra
):
    _, _, job = legacy_native_job
    reference = {"name": "transcriber", "version": "1.12"}
    remote.template["specification"]["application"] = reference
    remote.template["status"]["resolvedApplication"] = current_transcriber_application
    values = remote.template["specification"]["parameters"]
    values["jupyterLab"] = {"type": "boolean", "value": True}
    if extra:
        values[extra] = {"type": "file", "path": "/other/keep"}
    original_template = copy.deepcopy(remote.template)
    if extra:
        with pytest.raises(ValueError, match="extra input/initialization"):
            client.app.state.queue.process(job["id"])
        assert not remote.submissions and not remote.uploaded
    else:
        client.app.state.queue.process(job["id"])
        assert remote.uploaded == wav_bytes and len(remote.submissions) == 1
        submitted = remote.submissions[0]
        assert submitted["application"] == reference
        assert submitted["product"] == PRODUCT
        assert submitted["resources"] == original_template["specification"]["resources"]
        assert submitted["parameters"]["jupyterLab"] == {"type": "boolean", "value": False}
        assert submitted["parameters"]["inputFile"]["path"].endswith("/recording.wav")
        assert submitted["parameters"]["outputFormat"] == {"type": "text", "value": "json"}
    assert remote.template == original_template


@pytest.mark.parametrize("directory", [False, True], ids=["single", "shared-batch"])
def test_v26_native_parakeet_uses_declared_model_value(current_transcriber_application, directory):
    settings = Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="nvidia/parakeet-tdt-0.6b-v3",
        language="da",
        ucloud_transcriber={
            **SETUP,
            "template_job_id": "",
            "application": {"name": "transcriber", "version": "1.12"},
            "input_parameter": "inputFile",
            "output_parameter": "outputDir",
            "format_parameter": "outputFormat",
        },
    )
    parameters = native.application_parameters(current_transcriber_application)
    # Exact model choices observed through a read-only app metadata GET on 2026-10-30.
    parameters["model"]["options"] = [
        {"name": "whisper/" + model, "value": model}
        for model in ("base", "small", "medium", "large-v3", "large-v3-turbo")
    ] + [{"name": "nvidia/parakeet-tdt-0.6b-v3", "value": "parakeet"}]
    template = {
        "application": settings.ucloud_transcriber.application,
        "product": PRODUCT,
        "replicas": 1,
        "parameters": native.batch_overrides(current_transcriber_application, parameters),
        "resources": [],
    }
    frozen = copy.deepcopy((settings.model_dump(), template, parameters))
    if directory:
        specification, input_parameter = native.build_directory_specification(
            settings, template, parameters, "/drive/owned", "synthetic-batch"
        )
        assert input_parameter == "inputDir"
        assert "inputFile" not in specification["parameters"]
    else:
        specification = native.build_specification(
            settings, template, parameters, "/drive/owned", ".wav", "synthetic-job"
        )
        assert "inputFile" in specification["parameters"]
    assert specification["parameters"]["model"] == {"type": "text", "value": "parakeet"}
    assert native.capture_selector(settings, specification) == {
        "model": "parakeet",
        "language": "da",
    }
    assert (settings.model_dump(), template, parameters) == frozen


@pytest.mark.parametrize("change", ["wrong-app", "web", "parameter", "model"])
def test_v26_invalid_template_fails_before_upload(client, remote, legacy_native_job, change):
    _, _, job = legacy_native_job
    invocation = remote.template["status"]["resolvedApplication"]["invocation"]
    if change == "wrong-app":
        remote.template["specification"]["application"] = {"name": "other", "version": "1"}
    elif change == "web":
        invocation["applicationType"] = "WEB"
    elif change == "parameter":
        invocation["parameters"][0]["name"] = "different_input_name"
    else:
        invocation["parameters"][2]["options"] = [{"value": "small"}]
    with pytest.raises(ValueError):
        client.app.state.queue.process(job["id"])
    assert not remote.submissions and not remote.uploaded


def test_v26_unknown_submission_does_not_submit_twice(client, remote, native_job):
    _, _, job = native_job
    remote.unknown_submission = True
    with pytest.raises(ValueError, match="connection failed"):
        client.app.state.queue.process(job["id"])
    saved = client.app.state.store.job(job["id"])
    assert saved["params"]["ucloud_run"]["submission_attempted"]
    with pytest.raises(ValueError, match="outcome is unknown"):
        client.app.state.queue.process(job["id"])
    assert len(remote.submissions) == 1

    # Explicit absence confirmation permits retry, but cannot authorize later POSTs.
    store = client.app.state.store
    store.update_job(job["id"], state="failed")
    assert (
        client.post(f"/api/jobs/{job['id']}/reconcile", json={"not_created": True}).status_code
        == 200
    )
    with pytest.raises(ValueError, match="connection failed"):
        client.app.state.queue.process(job["id"])
    assert len(remote.submissions) == 2
    assert not store.job(job["id"])["params"]["ucloud_run"]["reconciled_not_created"]
    with pytest.raises(ValueError, match="outcome is unknown"):
        client.app.state.queue.process(job["id"])
    assert len(remote.submissions) == 2


@pytest.mark.parametrize("shutdown", [True, False])
def test_v27_shutdown_recovers_remote_job_cancel_only_owned_job(
    client,
    remote,
    native_job,
    native_lease,
    shutdown,
):
    _, recording, job = native_job
    worker, store = client.app.state.queue, client.app.state.store
    remote.states = ["RUNNING"]

    class StopOnWait(threading.Event):
        def wait(self, timeout=None):
            self.set()
            if shutdown:
                worker.stopping.set()
            return True

    worker.cancel_event = StopOnWait()
    with pytest.raises(Cancelled):
        worker.process(job["id"])
    terminations = [r for r in remote.calls if r.url.path == "/api/jobs/terminate"]
    assert len(terminations) == (0 if shutdown else 1)
    assert store.job(job["id"])["params"]["ucloud_run"]["job_id"] == "remote-job"
    if shutdown:
        worker.stopping.clear()
        worker.cancel_event = threading.Event()
        native_lease.close()
        worker.store = Store(store.root)  # Simulate another process opening the persisted library.
        remote.states = ["SUCCESS"]
        with worker.store.library_lock():
            worker.process(job["id"])
        assert len(remote.submissions) == 1
        assert len(worker.store.results(recording["id"])) == 1


def test_v27_error_redaction(client, remote, native_job):
    provider, _, _ = native_job
    remote.error_status = 401
    response = client.post("/api/providers/check-ucloud", json=provider)
    assert response.status_code == 400 and "401" in response.text
    assert SECRET not in response.text


@pytest.mark.parametrize("state", ["queued", "failed", "interrupted"])
def test_v27_cancel_queued_retry_terminates_recorded_remote_job(client, remote, native_job, state):
    _, _, job = native_job
    store = client.app.state.store
    job["params"]["ucloud_run"] = {"job_id": "remote-job"}
    store.update_job(job["id"], params=job["params"], state=state)
    client.app.state.queue.cancel(job["id"])
    assert store.job(job["id"])["state"] == "cancelled"
    assert any(r.url.path == "/api/jobs/terminate" for r in remote.calls)


def test_v27_websocket_binary_file_transfer(tmp_path, monkeypatch):
    path = tmp_path / "source.wav"
    data = b"audio" * 300000
    path.write_bytes(data)
    sent, received = [], iter([struct.pack(">Bi", 0, 0), struct.pack(">Bi", 5, 1)])

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send(self, frame):
            sent.append(frame)

        def recv(self, **kwargs):
            return next(received)

    def connect(endpoint, **kwargs):
        assert endpoint == "wss://files.invalid/upload?session=transfer"
        assert "additional_headers" not in kwargs
        return Socket()

    monkeypatch.setattr(native, "connect", connect)
    native.websocket_upload(
        "https://cloud.invalid",
        {
            "endpoint": "wss://files.invalid/upload?session=transfer",
        },
        path,
        lambda: None,
        lambda **kwargs: None,
    )
    assert struct.unpack(">BiqqI", sent[0])[0:3] == (4, 0, len(data))
    assert b"".join(frame[5:] for frame in sent[1:-1]) == data
    assert all(struct.unpack(">Bi", frame[:5]) == (2, 0) for frame in sent[1:-1])
    assert sent[-1] == bytes([6])


def test_upload_revoked_during_session_does_not_start_websocket(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.wav"
    path.write_bytes(b"synthetic")
    stopped = False

    class Response:
        def json(self):
            return {
                "responses": [{"protocol": "WEBSOCKET_V2", "endpoint": "wss://files.invalid/x"}]
            }

    class Client:
        def request(self, *args, **kwargs):
            nonlocal stopped
            stopped = True
            return Response()

    def guard():
        if stopped:
            raise ValueError("stopped")

    monkeypatch.setattr(native, "websocket_upload", lambda *args: pytest.fail("websocket started"))
    with pytest.raises(ValueError, match="stopped"):
        native.upload(
            Client(), "https://cloud.invalid", path, "/drive/file", guard, lambda **_: None
        )


def test_websocket_revoked_during_connect_sends_no_listing(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.wav"
    path.write_bytes(b"synthetic")
    stopped = False

    class Socket:
        def __enter__(self):
            nonlocal stopped
            stopped = True
            return self

        def __exit__(self, *args):
            pass

        def send(self, frame):
            pytest.fail("listing sent after revocation")

    def guard():
        if stopped:
            raise ValueError("stopped")

    monkeypatch.setattr(native, "connect", lambda *args, **kwargs: Socket())
    with pytest.raises(ValueError, match="stopped"):
        native.websocket_upload(
            "https://cloud.invalid",
            {"endpoint": "wss://files.invalid/x"},
            path,
            guard,
            lambda **_: None,
        )


def test_upload_revoked_after_websocket_does_not_start_verify(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.wav"
    path.write_bytes(b"synthetic")
    stopped = False

    class Response:
        def json(self):
            return {
                "responses": [{"protocol": "WEBSOCKET_V2", "endpoint": "wss://files.invalid/x"}]
            }

    class Client:
        def request(self, *args, **kwargs):
            return Response()

    def guard():
        if stopped:
            raise ValueError("stopped")

    def send(*args):
        nonlocal stopped
        stopped = True

    monkeypatch.setattr(native, "websocket_upload", send)
    monkeypatch.setattr(native, "verify_upload", lambda *args: pytest.fail("verify started"))
    with pytest.raises(ValueError, match="stopped"):
        native.upload(
            Client(), "https://cloud.invalid", path, "/drive/file", guard, lambda **_: None
        )


def test_upload_revoked_after_chunk_does_not_start_verify(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.wav"
    path.write_bytes(b"synthetic")
    stopped = False

    class Response:
        is_success = True

        def json(self):
            return {
                "responses": [
                    {
                        "protocol": "CHUNKED",
                        "endpoint": "https://files.invalid/x",
                        "token": "temporary",
                    }
                ]
            }

    class Client:
        def request(self, *args, **kwargs):
            return Response()

    class Separate:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *args, **kwargs):
            nonlocal stopped
            stopped = True
            return Response()

    def guard():
        if stopped:
            raise ValueError("stopped")

    monkeypatch.setattr(native.httpx, "Client", Separate)
    monkeypatch.setattr(native, "verify_upload", lambda *args: pytest.fail("verify started"))
    with pytest.raises(ValueError, match="stopped"):
        native.upload(
            Client(), "https://cloud.invalid", path, "/drive/file", guard, lambda **_: None
        )


def test_v18_native_identity_change_revokes_frozen_approval(client, native_job):
    provider, recording, job = native_job
    store = client.app.state.store
    store.execute(
        "UPDATE projects SET classification='protected' WHERE id=?", (recording["project_id"],)
    )
    provider["approved_for"] = ["protected"]
    provider["ucloud_transcriber"]["project_id"] = "other-project"
    assert client.put("/api/providers/" + provider["id"], json=provider).status_code == 200
    with pytest.raises(ValueError, match="explicitly approved"):
        client.app.state.queue.process(job["id"])


@pytest.fixture
def cleanup_run(client, credentials, wav_bytes, native_lease, remote, monkeypatch):
    setup = {**fresh_native_setup(remote, monkeypatch), "cleanup_after_success": True}
    provider, recording = native_selection(client, wav_bytes, setup)
    response = client.post(
        "/api/transcriptions",
        json={"provider_ids": [provider["id"]], "recording_ids": [recording["id"]]},
    )
    assert response.status_code == 202, response.text
    store = client.app.state.store
    accepted = response.json()[0]
    assert accepted["params"]["input_intent"]["phase"] == "waiting"
    assert store.claim_job("transcribe") is None
    job = inspect_and_materialize(store, credentials, [accepted["id"]])[0]
    return recording, job


@pytest.fixture
def retained_legacy_run(client, wav_bytes, native_lease, remote):
    provider, recording = native_selection(client, wav_bytes)
    return recording, frozen_native_job(client, provider, recording)


def trash_fake(remote, folder, *, accept=True):
    """UCloud lists the owned run folder in its parent until a trash request is accepted."""
    state = {"accept": accept, "trashed": False, "requests": []}
    original = remote.handle

    def handle(request):
        if request.method == "DELETE":
            raise AssertionError("UCloud storage providers implement no permanent DELETE.")
        if request.url.path == "/api/files/trash":
            state["requests"].append(json.loads(request.content))
            if not state["accept"]:
                return httpx.Response(403, text=SECRET)
            state["trashed"] = True
            return httpx.Response(200, json={"responses": [{}]})
        if (
            request.url.path == "/api/files/browse"
            and request.url.params["path"] == folder.rsplit("/", 1)[0]
        ):
            items = [] if state["trashed"] else [{"id": folder}]
            return httpx.Response(200, json={"items": [{"id": "/drive/cbs/other"}, *items]})
        return original(request)

    remote.handle = handle
    return state


def test_v57_trashes_only_owned_folder_after_validated_local_commit(client, remote, cleanup_run):
    recording, job = cleanup_run
    store = client.app.state.store
    folder = "/drive/cbs/cbs-" + job["id"]
    state = trash_fake(remote, folder)
    client.app.state.queue.process(job["id"])
    assert state["requests"] == [{"items": [{"id": folder}]}]
    raw = store.directory(recording["id"]) / ("ucloud-" + job["id"] + ".json")
    assert json.loads(raw.read_text(encoding="utf-8")) == PAYLOAD
    assert store.job(job["id"])["params"]["ucloud_run"]["cleanup_state"] == "trashed"
    client.app.state.queue.process(job["id"])
    assert len(state["requests"]) == 1 and len(remote.submissions) == 1
    assert len(store.results(recording["id"])) == 1


def test_v57_rejected_trash_retains_success_and_cleanup_only_retry(
    client, remote, cleanup_run, monkeypatch
):
    recording, job = cleanup_run
    store = client.app.state.store
    monkeypatch.setattr(native, "TRASH_CONFIRM_DELAYS", (0,))
    state = trash_fake(remote, "/drive/cbs/cbs-" + job["id"], accept=False)
    client.app.state.queue.process(job["id"])
    store.update_job(job["id"], state="completed")
    assert len(store.results(recording["id"])) == 1
    listed = client.get("/api/jobs").json()[0]
    assert listed["ucloud_cleanup_state"] == "pending"
    assert "HTTP 403" in listed["ucloud_cleanup_error"]
    assert SECRET not in json.dumps(listed)
    # Do not lose the cleanup journal when its Activity item would otherwise age out.
    for _ in range(201):
        old = store.add_job(recording["id"], "media")
        store.update_job(old["id"], state="completed")
    assert any(j["id"] == job["id"] for j in client.get("/api/jobs").json())
    state["accept"] = True
    assert client.post("/api/jobs/" + job["id"] + "/cleanup-ucloud").json() == {
        "cleanup_state": "trashed"
    }
    assert len(state["requests"]) == 2 and len(remote.submissions) == 1
    assert len(store.results(recording["id"])) == 1


def test_v57_retry_after_unrecorded_trash_confirms_absence(client, remote, cleanup_run):
    _, job = cleanup_run
    folder = "/drive/cbs/cbs-" + job["id"]
    state = trash_fake(remote, folder, accept=False)
    state["trashed"] = True  # An earlier request moved it, but its receipt was lost.
    client.app.state.queue.process(job["id"])
    run = client.app.state.store.job(job["id"])["params"]["ucloud_run"]
    assert run["cleanup_state"] == "trashed" and "cleanup_error" not in run


def test_v57_folder_still_listed_stays_pending(client, remote, cleanup_run, monkeypatch):
    _, job = cleanup_run
    monkeypatch.setattr(native, "TRASH_CONFIRM_DELAYS", (0, 0))
    folder = "/drive/cbs/cbs-" + job["id"]
    trash_fake(remote, folder)
    original = remote.handle

    def stale(request):
        if request.url.path == "/api/files/browse" and request.url.params["path"] == "/drive/cbs":
            return httpx.Response(200, json={"items": [{"id": folder}]})
        return original(request)

    remote.handle = stale
    client.app.state.queue.process(job["id"])
    run = client.app.state.store.job(job["id"])["params"]["ucloud_run"]
    assert run["cleanup_state"] == "pending" and "still listed" in run["cleanup_error"]


@pytest.mark.parametrize("damage", ["raw", "no-result", "unowned", "failed", "uncertain"])
def test_v57_never_deletes_without_recoverable_success(client, remote, retained_legacy_run, damage):
    recording, job = retained_legacy_run
    store = client.app.state.store
    # Historical retention settings stay frozen until the explicit cleanup-only test action.
    client.app.state.queue.process(job["id"])
    saved = store.job(job["id"])
    saved["params"]["settings"]["ucloud_transcriber"]["cleanup_after_success"] = True
    run = saved["params"]["ucloud_run"]
    if damage == "raw":
        (store.directory(recording["id"]) / ("ucloud-" + job["id"] + ".json")).write_bytes(
            b"invalid"
        )
    elif damage == "no-result":
        store.execute("DELETE FROM transcript_results WHERE job_id=?", (job["id"],))
    elif damage == "unowned":
        run["owned_folder"] = "/drive/cbs"
    elif damage == "failed":
        run["remote_state"] = "FAILURE"
    else:
        run.pop("job_id")
    store.update_job(job["id"], params=saved["params"])
    with pytest.raises(ValueError):
        native.cleanup_owned(store, client.app.state.credentials, store.job(job["id"]))
    diagnostic = store.job(job["id"])["params"]["ucloud_run"]
    assert diagnostic["cleanup_state"] == (
        "pending" if damage in {"raw", "no-result"} else "retained"
    )
    assert diagnostic["cleanup_stage"] == (
        "local_validation" if damage in {"raw", "no-result"} else "preconditions"
    )
    assert diagnostic["cleanup_error_code"]
    assert diagnostic["cleanup_attempted_at"]
    assert not any(r.url.path == "/api/files/trash" for r in remote.calls)


def test_v57_new_default_does_not_change_legacy_snapshots(client):
    assert not Settings(
        provider_type="ucloud", base_url="https://cloud.invalid"
    ).ucloud_transcriber.cleanup_after_success
    new = client.post(
        "/api/providers",
        json={"name": "Native", "provider_type": "ucloud", "base_url": "https://cloud.invalid"},
    ).json()
    assert new["ucloud_transcriber"]["cleanup_after_success"]


def test_v57_pending_journal_precedes_result_commit(client, remote, cleanup_run, monkeypatch):
    recording, job = cleanup_run
    store, add = client.app.state.store, client.app.state.store.add_result

    def crash_after_commit(*args, **kwargs):
        add(*args, **kwargs)
        assert store.job(job["id"])["params"]["ucloud_run"]["cleanup_state"] == "pending"
        raise OSError("Synthetic crash after commit")

    monkeypatch.setattr(store, "add_result", crash_after_commit)
    with pytest.raises(OSError):
        client.app.state.queue.process(job["id"])
    store.update_job(job["id"], state="interrupted")
    assert store.results(recording["id"])
    assert client.get("/api/jobs").json()[0]["ucloud_cleanup_state"] == "pending"
    from cbs_transcriber.remote_jobs import unresolved

    assert unresolved(store.job(job["id"]))
    assert not any(r.url.path == "/api/files/trash" for r in remote.calls)


def test_v57_sync_failure_retains_remote_files(client, remote, cleanup_run, monkeypatch):
    _, job = cleanup_run
    store, flush = client.app.state.store, native.os.fsync

    def fail(*args):
        # Only the post-commit cleanup flush fails; evidence writes before it stay durable.
        if store.all("SELECT id FROM transcript_results WHERE job_id=?", (job["id"],)):
            raise OSError("Synthetic disk failure")
        return flush(*args)

    monkeypatch.setattr(native.os, "fsync", fail)
    client.app.state.queue.process(job["id"])
    assert (
        client.app.state.store.job(job["id"])["params"]["ucloud_run"]["cleanup_state"] == "pending"
    )
    assert not any(r.url.path == "/api/files/trash" for r in remote.calls)


def test_v57_fresh_folder_conflict_is_not_owned(client, remote, cleanup_run):
    _, job = cleanup_run
    handle = remote.handle

    def conflict(request):
        if request.url.path == "/api/files/folder":
            return httpx.Response(409)
        return handle(request)

    remote.handle = conflict
    with pytest.raises(ValueError, match="ownership receipt"):
        client.app.state.queue.process(job["id"])
    saved = client.app.state.store.job(job["id"])["params"].get("ucloud_run", {})
    assert not saved.get("folders_created") and not saved.get("owned_folder")
    assert not remote.uploaded and not remote.submissions


def test_v86_cleanup_records_local_validation_failure(client, remote, retained_legacy_run):
    recording, job = retained_legacy_run
    store = client.app.state.store
    client.app.state.queue.process(job["id"])
    saved = store.job(job["id"])
    saved["params"]["settings"]["ucloud_transcriber"]["cleanup_after_success"] = True
    saved["params"]["ucloud_run"]["cleanup_state"] = "pending"
    store.update_job(job["id"], params=saved["params"])
    raw = store.directory(recording["id"]) / ("ucloud-" + job["id"] + ".json")
    raw.write_bytes(b"invalid synthetic response")

    with pytest.raises(ValueError):
        native.cleanup_owned(store, client.app.state.credentials, store.job(job["id"]))

    receipt = store.job(job["id"])["params"]["ucloud_run"]
    assert receipt["cleanup_state"] == "pending"
    assert receipt["cleanup_stage"] == "local_validation"
    assert receipt["cleanup_error_code"] == "local_response_invalid"
    assert "local response" in receipt["cleanup_error"]
    assert receipt["cleanup_attempted_at"]
    assert not any(r.url.path == "/api/files/trash" for r in remote.calls)


def test_v86_cleanup_timeout_has_safe_stage_diagnostic(client, remote, cleanup_run, monkeypatch):
    _, job = cleanup_run

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("upstream private detail " + SECRET)

    monkeypatch.setattr(native, "trash_owned_folder", timeout)
    client.app.state.queue.process(job["id"])
    receipt = client.app.state.store.job(job["id"])["params"]["ucloud_run"]
    assert receipt["cleanup_state"] == "pending"
    assert receipt["cleanup_stage"] == "trash"
    assert receipt["cleanup_error_code"] == "connection_timeout"
    assert "timed out" in receipt["cleanup_error"]
    assert "upstream private detail" not in json.dumps(receipt)
    assert SECRET not in json.dumps(receipt)


@pytest.mark.parametrize("item", [None, {}, {"id": None}, {"id": 123}])
def test_cleanup_cannot_confirm_absence_from_malformed_parent_items(item):
    class Client:
        def browse_files(self, *args, **kwargs):
            return {"items": [item]}

    with pytest.raises(ValueError, match="malformed folder metadata"):
        native.folder_listed(Client(), "/drive/owned")
