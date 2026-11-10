"""API-only UCloud enhancement against a simulated UCloud: no network, SSH, tokens or jobs."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from urllib.parse import parse_qs, quote

import httpx
import pytest
from admission_support import inspect_and_materialize, write_original_wav

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import input_admission, providers, runtime_compute
from cbs_transcriber.enhancement import EnhancementOptions
from cbs_transcriber.enhancement_worker import MODELS, cache_status
from cbs_transcriber.media import Cancelled
from cbs_transcriber.transcripts import normalize_segments

REAL_CLIENT = httpx.Client
SECRET = "synthetic-api-enhancement-secret"
PRODUCT = {"id": "gpu-nvidia-b200-1-gpu", "category": "gpu", "provider": "ucloud"}
SETUP = {
    "project_id": "project",
    "storage_path": "/drive/cbs",
    "application": {"name": "transcriber", "version": "1.12"},
    "product": PRODUCT,
    "input_parameter": "inputFile",
    "output_parameter": "outputDir",
    "model_parameter": "model",
    "language_parameter": "language",
    "format_parameter": "outputFormat",
    "cleanup_after_success": True,
}
ALIGNMENT = {
    "passages": [
        {
            "id": "0",
            "words": [
                {"word": "Hej", "start": 0, "end": 1, "score": 0.9},
                {"word": "København.", "start": 1, "end": 3, "score": 0.9},
            ],
        }
    ]
}

TRANSCRIPTION = {
    "segments": [
        {
            "text": "Hej København.",
            "start": 0,
            "end": 3,
            "avg_logprob": -0.31,
            "no_speech_prob": 0.01,
            "compression_ratio": 1.2,
        }
    ]
}

# Observed read-only on 23 Oct 2026: the Terminal group lists these flavours, each with a
# "Batch processing" batchScript input (Bash .sh).
TERMINAL_SEARCH = {"name": "terminal-almalinux", "version": "Aug2026", "groupId": 55}
TERMINAL_GROUP = {
    "metadata": {"id": 55},
    "status": {
        "applications": [
            {"metadata": {"name": "terminal-almalinux", "version": "Aug2026"}},
            {"metadata": {"name": "terminal-ubuntu", "version": "Sep2026"}},
        ]
    },
}
TERMINAL_APP = {
    "metadata": {"name": "terminal-ubuntu", "version": "Sep2026"},
    "invocation": {
        "parameters": [
            {"name": "initScript", "type": "input_file", "optional": True},
            {"name": "batchScript", "type": "input_file", "optional": True},
        ]
    },
}


class FakeUCloud:
    """In-memory drive plus a supervisor that follows the real file handshake."""

    def __init__(self, application):
        self.application = application
        self.files, self.folders = {}, {"/drive", "/drive/cbs"}
        self.submissions, self.terminations, self.trashed = [], [], []
        self.uploaded = []
        self.state, self.end_state = "RUNNING", "RUNNING"
        self.cache = {"models": {key: True for key in MODELS}, "punkt_tab": True}
        self.doctor = {
            "ready": True,
            "version": "3.8.6",
            "gpu": {"available": True, "devices": [{"memory_gb": 23}]},
        }
        self.fail_code = None
        self.phase_at_audio = None
        self.submission_error = None
        self.run = self.generation = None
        self.speakers_at_submit = None
        self.fail_uploads = {}
        self.refuse_sessions, self.refused = set(), 0

    def write(self, name, value):
        self.files[self.run + "/" + name] = json.dumps(value).encode()

    def phase(self):
        raw = self.files.get((self.run or "") + "/supervisor.json")
        return json.loads(raw)["phase"] if raw else None

    def step(self):
        if self.run is None or self.state != "RUNNING":
            return
        phase = self.phase()
        if phase is None:
            if self.fail_code:
                self.write("supervisor.json", {"phase": "failed", "code": self.fail_code})
                self.state = "SUCCESS"
                return
            self.write("doctor.json", self.doctor)
            repo, revision = MODELS["speakers"]
            marker = "models--" + repo.replace("/", "--") + "/snapshots/" + revision
            seeded = any(p.endswith(marker + "/.cbs-complete") for p in self.files)
            cache = json.loads(json.dumps(self.cache))
            cache["models"]["speakers"] = cache["models"]["speakers"] or seeded
            self.write("cache.json", cache)
            self.write("supervisor.json", {"phase": "ready", "generation": self.generation})
        elif phase == "ready" and self.run + "/manifest.sha256" in self.files:
            for line in self.files[self.run + "/manifest.sha256"].decode().splitlines():
                digest, name = line.split("  ")
                assert hashlib.sha256(self.files[self.run + "/" + name]).hexdigest() == digest
            request = json.loads(self.files[self.run + "/request.json"])
            if request["options"].get("transcribe"):
                self.write("transcription.json", TRANSCRIPTION)
            if request["options"].get("retranscribe"):
                # Only bounds arrive; the reviewed wording is never uploaded.
                assert all(p["text"] == "" for p in request["passages"])
                self.write(
                    "retranscription.json", {"passages": [{"id": "0", "text": "Hej København."}]}
                )
            if request["options"]["align"]:
                self.write("alignment.json", ALIGNMENT)
            if request["options"]["diarize"]:
                self.write("diarization.json", {"turns": [{"start": 0, "end": 3, "speaker": "A"}]})
            self.write("remote-status.json", {"state": "complete"})
            self.write("supervisor.json", {"phase": "finished", "generation": self.generation})
            self.state = self.end_state

    def children(self, parent):
        return sorted(
            p for p in self.folders | set(self.files) if str(PurePosixPath(p).parent) == parent
        )

    def handle(self, request):
        url = request.url
        query = parse_qs(url.query.decode())
        if url.host == "files.invalid":
            assert "authorization" not in request.headers
            if url.path == "/upload":
                target = query["target"][0]
                if target.endswith("/audio.wav"):
                    self.phase_at_audio = self.phase()
                for suffix, count in self.fail_uploads.items():
                    if target.endswith(suffix) and count:
                        self.fail_uploads[suffix] = count - 1
                        return httpx.Response(502)
                offset = int(request.headers["Chunked-Upload-Offset"])
                self.files[target] = self.files.get(target, b"")[:offset] + request.content
                return httpx.Response(200)
            return httpx.Response(200, content=self.files[query["path"][0]])
        assert url.host == "cloud.invalid"
        assert request.headers["Authorization"] == "Bearer " + SECRET
        self.step()
        path = url.path
        body = json.loads(request.content) if request.content else {}
        item = (body.get("items") or [{}])[0]
        if path == "/api/jobs/retrieveProducts":
            return httpx.Response(
                200,
                json={
                    "productsByProvider": {
                        "ucloud": [
                            {
                                "product": {
                                    "name": runtime_compute.MIG_PRODUCT["id"],
                                    "category": {
                                        "name": "gpu-nvidia-b200",
                                        "provider": "ucloud",
                                        "productType": "COMPUTE",
                                    },
                                    "gpu": 1,
                                    "gpuModel": "B200-mig.1g.23gb",
                                    "fraction": {"numerator": 1, "denominator": 7},
                                    "memoryInGigs": 36,
                                },
                                "support": {
                                    "product": dict(runtime_compute.MIG_PRODUCT),
                                    "docker": {"enabled": True},
                                    "queueStatus": "AVAILABLE",
                                },
                            }
                        ]
                    }
                },
            )
        if path == "/api/accounting/v2/browseWallets":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "owner": {"projectId": "project"},
                            "paysFor": {
                                "name": "gpu-nvidia-b200",
                                "provider": "ucloud",
                                "productType": "COMPUTE",
                            },
                            "maxUsable": 100,
                        }
                    ],
                    "next": None,
                },
            )
        if path == "/api/hpc/apps/search":
            return httpx.Response(200, json={"items": [{"metadata": TERMINAL_SEARCH}]})
        if path == "/api/hpc/apps/retrieveGroups":
            return httpx.Response(200, json=TERMINAL_GROUP)
        if path == "/api/hpc/apps/byNameAndVersion":
            if url.params["appName"] == "terminal-ubuntu":
                return httpx.Response(200, json=TERMINAL_APP)
            return httpx.Response(200, json=self.application)
        if path == "/api/files/folder":
            if item["id"] in self.folders or item["id"] in self.files:
                return httpx.Response(409)
            if str(PurePosixPath(item["id"]).parent) not in self.folders:
                return httpx.Response(400, text="Could not find file")
            self.folders.add(item["id"])
            return httpx.Response(200, json={})
        if path == "/api/files/upload":
            assert str(PurePosixPath(item["id"]).parent) in self.folders
            if any(item["id"].endswith(suffix) for suffix in self.refuse_sessions):
                self.refused += 1
                return httpx.Response(403)
            self.uploaded.append(item["id"])
            endpoint = "https://files.invalid/upload?target=" + quote(item["id"])
            return httpx.Response(
                200,
                json={"responses": [{"endpoint": endpoint, "protocol": "CHUNKED", "token": "t"}]},
            )
        if path == "/api/files/retrieve":
            target = url.params["id"]
            if target not in self.files:
                return httpx.Response(400, text="Could not find file")
            return httpx.Response(
                200, json={"status": {"type": "FILE", "sizeInBytes": len(self.files[target])}}
            )
        if path == "/api/files/browse":
            if url.params["path"] not in self.folders:
                return httpx.Response(400, text="Could not find file")
            items = [{"id": child} for child in self.children(url.params["path"])]
            return httpx.Response(200, json={"items": items, "next": None})
        if path == "/api/files/download":
            if item["id"] not in self.files:
                return httpx.Response(400, text="Could not find file")
            endpoint = "https://files.invalid/download?path=" + quote(item["id"])
            return httpx.Response(200, json={"responses": [{"endpoint": endpoint}]})
        if path == "/api/files/trash":
            self.trashed.append(item["id"])
            for name in [p for p in self.files if p.startswith(item["id"] + "/")]:
                del self.files[name]
            self.folders = {p for p in self.folders if not p.startswith(item["id"])}
            return httpx.Response(200, json={"responses": [{}]})
        if path == "/api/jobs" and request.method == "POST":
            if self.submission_error:
                raise self.submission_error
            spec = item
            for value in spec["parameters"].values():
                if value.get("type") == "file":
                    # UCloud rejects file/directory parameters that do not exist.
                    assert value["path"] in self.files or value["path"] in self.folders, value
            for resource in spec["resources"]:
                assert resource["path"] in self.folders, resource
            self.submissions.append(spec)
            self.speakers_at_submit = any(p.endswith("/.cbs-complete") for p in self.files)
            self.generation = spec["name"].rsplit(" ", 1)[1]
            self.run = next(
                r["path"] for r in spec["resources"] if "/cbs-enhancement-" in r["path"]
            )
            return httpx.Response(200, json={"responses": [{"id": "remote-enhancement"}]})
        if path == "/api/jobs/retrieve":
            return httpx.Response(
                200,
                json={
                    "id": "remote-enhancement",
                    "specification": self.submissions[-1],
                    "status": {"state": self.state},
                },
            )
        if path == "/api/jobs/terminate":
            self.terminations.append(item["id"])
            self.state = "CANCELLED"
            return httpx.Response(200, json={})
        raise AssertionError("Unexpected mocked request: " + path)


@pytest.fixture
def cloud(monkeypatch, current_transcriber_application):
    # These cases retain the frozen singleton protocol. Shared allocation execution has
    # separate controller/coordinator tests and must not weaken legacy recovery coverage.
    monkeypatch.setattr("cbs_transcriber.runtime_batches.freeze_plan", lambda *_: None)
    fake = FakeUCloud(current_transcriber_application)
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: REAL_CLIENT(*a, **kw, transport=httpx.MockTransport(fake.handle)),
    )
    monkeypatch.setattr(api, "POLL_SECONDS", 0)
    monkeypatch.setattr("cbs_transcriber.ucloud_native.TRASH_CONFIRM_DELAYS", (0,))
    monkeypatch.setattr(
        "cbs_transcriber.enhancement.media.ffmpeg",
        lambda args, *a: Path(args[-1]).write_bytes(b"synthetic enhancement audio"),
    )
    return fake


@pytest.fixture
def connection(client, credentials):
    # Singleton transport/recovery cases exercise the explicitly disabled standing choice.
    client.put("/api/enhancement-settings", json={"prepare_ucloud_models": False})
    return providers.save_profile(
        client.app.state.store,
        credentials,
        "UCloud",
        providers.Settings(
            provider_type="ucloud",
            base_url="https://cloud.invalid",
            model="large-v3",
            approved_for=["protected"],
            ucloud_transcriber=SETUP,
        ),
        api_key=SECRET,
        remember=False,
    )


def enqueue(client, classification="standard", **options):
    store = client.app.state.store
    project = store.create_project("API enhancement", classification)
    recording = store.create_recording(project["id"], "synthetic.wav")
    write_original_wav(store, recording)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Hej København.", "start": 0, "end": 3}]),
        {},
        "Reviewed",
        {},
    )
    response = client.post(
        f"/api/recordings/{recording['id']}/enhancements",
        json={"result_id": result["id"], "revision": 1, "execution": "ucloud", **options},
    )
    return response, recording, result


def approve(client, values):
    """The UCloud tab applies Protected policy to every model profile of the connection."""
    store, credentials = client.app.state.store, client.app.state.credentials
    for row in store.all("SELECT id,name FROM providers"):
        settings = providers.Settings.model_validate(
            providers.profile(store, row["id"])["settings"]
        )
        if settings.provider_type == "ucloud":
            settings.approved_for = values
            providers.save_profile(store, credentials, row["name"], settings, identifier=row["id"])


def synthetic_speakers(monkeypatch, files=None):
    """Pin synthetic speaker files (the real ones are gated) and return a cache writer."""
    files = files or {"config.yaml": b"pipeline: synthetic"}
    monkeypatch.setattr(
        api,
        "SPEAKER_FILES",
        {name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in files.items()},
    )

    def write(cache):
        repo, revision = MODELS["speakers"]
        snapshot = cache / ("models--" + repo.replace("/", "--")) / "snapshots" / revision
        for name, data in files.items():
            (snapshot / name).parent.mkdir(parents=True, exist_ok=True)
            (snapshot / name).write_bytes(data)
        return snapshot

    return write


def runtime_ready(cloud):
    folder = SETUP["storage_path"] + "/cbs-runtime/" + api.runtime_id()
    cloud.folders |= {SETUP["storage_path"] + "/cbs-runtime", folder}
    cloud.files[folder + "/stamp.json"] = json.dumps(
        {"requirements_sha256": api.requirements_hash()}
    ).encode()
    return folder


def test_v85_api_enhancement_uses_provider_connection_without_ssh_and_appends_once(
    client, cloud, connection
):
    with client.app.state.store.library_lock():
        runtime = runtime_ready(cloud)
        response, recording, original = enqueue(client)
        assert response.status_code == 202, response.text
        pending = response.json()
        assert "input_intent" in pending["params"]
        [job] = inspect_and_materialize(
            client.app.state.store, client.app.state.credentials, [pending["id"]]
        )
        store, queue = client.app.state.store, client.app.state.queue
        assert job["params"]["ucloud_api"]["provider_id"] == connection["id"]
        assert job["params"]["enhancement"]["device"] == "cuda"
        assert "cloud_settings" not in job["params"] and SECRET not in json.dumps(job)
        queue.process(job["id"])
        queue.process(job["id"])
        assert len(cloud.submissions) == 1
        spec = cloud.submissions[0]
        run = SETUP["storage_path"] + "/cbs-enhancement-" + job["id"]
        assert spec["sshEnabled"] is False and spec["product"] == runtime_compute.MIG_PRODUCT
        assert job["params"]["ucloud_api"]["runtime_product"] == runtime_compute.MIG_PRODUCT
        assert job["params"]["ucloud_api"]["runtime_compute_policy"] == "mig-v1"
        assert job["params"]["ucloud_api"]["settings"]["ucloud_transcriber"]["product"] == PRODUCT
        assert spec["name"] == "CBS enhancement " + job["id"]
        # ADR 0055: a Terminal batch job runs only the supervisor; no Transcriber program.
        assert spec["application"] == {"name": "terminal-ubuntu", "version": "Sep2026"}
        assert spec["parameters"] == {
            "batchScript": {"type": "file", "path": run + "/supervisor.sh"}
        }
        assert {"type": "file", "path": run, "readOnly": False} in spec["resources"]
        assert {"type": "file", "path": runtime, "readOnly": True} in spec["resources"]
        assert store.job(job["id"])["params"]["ucloud_run"]["script_application"] == {
            "name": "terminal-ubuntu",
            "version": "Sep2026",
        }
        # Readiness precedes any private upload; checksums cover the uploaded inputs.
        assert cloud.phase_at_audio == "ready"
        results = store.results(recording["id"])
        assert len(results) == 2
        assert store.recording(recording["id"])["active_transcript_id"] == original["id"]
        enhanced = store.result(recording["id"], results[-1]["id"])
        assert enhanced["segments"][0]["word_timing"]
        assert enhanced["source"]["transport"] == "api"
        assert enhanced["source"]["runtime_id"] == api.runtime_id()
        # The run folder holds audio: it moves to Trash; the shared runtime stays.
        assert cloud.trashed == [run] and runtime in cloud.folders
        assert run + "/requirements.txt" not in cloud.uploaded
        assert cloud.uploaded.index(run + "/supervisor.sh") < cloud.uploaded.index(
            run + "/audio.wav"
        )
        saved = store.job(job["id"])["params"]["ucloud_run"]
        assert saved["cleanup_state"] == "trashed" and saved["termination_requested"]
        assert not any(SECRET.encode() in data for data in cloud.files.values())


def test_v85_supervisor_script_is_credential_free_lf_and_valid_bash(tmp_path):
    settings = providers.Settings(
        provider_type="ucloud", base_url="https://cloud.invalid", ucloud_transcriber=SETUP
    )
    script = api.supervisor_script(settings, "abc123", prepare=True)
    assert "\r" not in script and "__" not in script.replace("__name__", "")
    assert "RUN=/work/cbs-enhancement-abc123" in script
    assert f"RT=/work/{api.runtime_id()}" in script and "PREPARE=1" in script
    assert api.UV_SHA256 in script and api.requirements_hash() in script
    assert "unset HF_TOKEN" in script and "HF_HUB_OFFLINE=1" in script
    bash = shutil.which("bash")
    # On Windows, PATH may resolve to the WSL launcher in System32 (slow, possibly no Linux).
    if bash and "system32" not in bash.lower():
        path = tmp_path / "supervisor.sh"
        path.write_text(script, encoding="utf-8", newline="\n")
        assert subprocess.run([bash, "-n", str(path)], capture_output=True).returncode == 0


def test_v85_unprepared_runtime_fails_before_any_folder_upload_or_job(client, cloud, connection):
    with client.app.state.store.library_lock():
        store = client.app.state.store
        # Metadata is accepted first; source inspection/preflight cannot grant preparation consent.
        asked, _, _ = enqueue(client)
        assert asked.status_code == 202, asked.text
        [failed] = inspect_and_materialize(
            store, client.app.state.credentials, [asked.json()["id"]]
        )
        intent = failed["params"]["input_intent"]
        assert failed["state"] == "failed" and intent["phase"] == "terminal"
        assert intent["error"]["code"] == "needs_confirmation"
        assert input_admission.projection(store, failed["id"])["can_retry"] is False
        with pytest.raises(input_admission.AdmissionError, match="consent"):
            input_admission.retry(store, failed["id"], epoch=intent["epoch"], phase=intent["phase"])
        assert not store.all("SELECT id FROM runtime_batch_runs")
        assert not cloud.submissions and not cloud.uploaded
        # A newly accepted job while the runtime exists still stops if readiness is lost later.
        runtime = runtime_ready(cloud)
        response, _, _ = enqueue(client)
        assert response.status_code == 202, response.text
        [job] = inspect_and_materialize(
            store, client.app.state.credentials, [response.json()["id"]]
        )
        assert job["state"] == "queued" and "input_intent" not in job["params"]
        del cloud.files[runtime + "/stamp.json"]
        with pytest.raises(ValueError, match="not prepared"):
            client.app.state.queue.process(job["id"])
        assert not cloud.submissions
        assert not any("cbs-enhancement-" in p for p in [*cloud.files, *cloud.folders])


def test_v85_explicit_preparation_mounts_runtime_writable_and_uploads_lock(
    client, cloud, connection
):
    with client.app.state.store.library_lock():
        response, recording, _ = enqueue(client, prepare_runtime=True)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        job = _admitted[0]
        client.app.state.queue.process(job["id"])
        spec = cloud.submissions[0]
        runtime = SETUP["storage_path"] + "/cbs-runtime/" + api.runtime_id()
        assert runtime in cloud.folders
        assert {"type": "file", "path": runtime, "readOnly": False} in spec["resources"]
        assert spec["timeAllocation"]["hours"] == 7  # six-hour default plus one-time preparation
        run = SETUP["storage_path"] + "/cbs-enhancement-" + job["id"]
        assert run + "/requirements.txt" in cloud.uploaded
        assert cloud.trashed == [run]


def test_v85_missing_runtime_models_stop_before_private_upload(client, cloud, connection):
    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        cloud.cache["models"]["da"] = False
        response, recording, _ = enqueue(client)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        job = _admitted[0]
        with pytest.raises(ValueError, match="lacks cached models"):
            client.app.state.queue.process(job["id"])
        run = SETUP["storage_path"] + "/cbs-enhancement-" + job["id"]
        assert run + "/audio.wav" not in cloud.files and run + "/request.json" not in cloud.files
        assert cloud.terminations == ["remote-enhancement"]
        assert len(client.app.state.store.results(recording["id"])) == 1


@pytest.mark.parametrize("memory", [None, 4, "23"])
def test_mig_readiness_stops_the_owned_job_before_upload_when_memory_is_unverified(
    client, cloud, connection, memory
):
    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        cloud.doctor["gpu"]["devices"][0]["memory_gb"] = memory
        response, recording, _ = enqueue(client)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        assert response.status_code == 202, response.text
        job = _admitted[0]
        with pytest.raises(ValueError, match="first CUDA device"):
            client.app.state.queue.process(job["id"])
        assert cloud.submissions[0]["product"] == runtime_compute.MIG_PRODUCT
        assert cloud.terminations == ["remote-enhancement"]
        assert not any(path.endswith("/audio.wav") for path in cloud.uploaded)
        assert len(client.app.state.store.results(recording["id"])) == 1


def test_legacy_frozen_job_keeps_the_original_full_gpu_product(client, cloud, connection):
    from cbs_transcriber.enhancement import EnhancementOptions

    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        store = client.app.state.store
        project = store.create_project("Historical full-GPU enhancement")
        recording = store.create_recording(project["id"], "synthetic.wav")
        write_original_wav(store, recording)
        store.execute(
            "UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],)
        )
        result = store.add_result(
            recording["id"],
            normalize_segments([{"text": "Hej København.", "start": 0, "end": 3}]),
            {},
            "Reviewed",
            {},
        )
        # Seed the old contract explicitly; never remove policy from a new accepted snapshot.
        job = store.enqueue_enhancement(
            recording["id"],
            result["id"],
            1,
            {
                "enhancement": EnhancementOptions(execution="ucloud", device="cuda").model_dump(),
                "provider_name": "UCloud WhisperX enhancement",
                "ucloud_api": {
                    "provider_id": connection["id"],
                    "settings": providers.profile(store, connection["id"])["settings"],
                    "runtime_id": api.runtime_id(),
                    "prepare_runtime": False,
                },
            },
        )
        assert "input_acceptance" not in job["params"] and "audio_input" not in job["params"]
        # Legacy jobs never inherited a VRAM floor or a different compute identity on retry.
        cloud.doctor["gpu"].pop("devices")
        client.app.state.queue.process(job["id"])
        assert cloud.submissions[0]["product"] == PRODUCT


def test_v85_cached_local_speaker_model_seeds_runtime_without_token(
    client, cloud, connection, monkeypatch, tmp_path
):
    with client.app.state.store.library_lock():
        runtime = runtime_ready(cloud)
        cloud.cache["models"]["speakers"] = False
        repo, revision = MODELS["speakers"]
        write = synthetic_speakers(
            monkeypatch,
            {
                "config.yaml": b"pipeline: synthetic",
                "embedding/pytorch_model.bin": b"synthetic weights",
            },
        )
        snapshot = write(tmp_path)
        (snapshot / "README.md").write_text("not pinned", encoding="utf-8")
        monkeypatch.setattr(api, "_local_hub_cache", lambda: tmp_path)
        response, recording, _ = enqueue(client, align=False, diarize=True)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        client.app.state.queue.process(response.json()["id"])
        # Copied before the GPU job was requested (ADR 0057).
        assert cloud.speakers_at_submit is True
        base = runtime + "/hf/hub/models--" + repo.replace("/", "--") + "/snapshots/" + revision
        assert cloud.files[base + "/config.yaml"] == b"pipeline: synthetic"
        assert cloud.files[base + "/embedding/pytorch_model.bin"] == b"synthetic weights"
        assert base + "/README.md" not in cloud.files
        # The completion marker is written last, so an interrupted copy is never "cached".
        seeded = [t for t in cloud.uploaded if t.startswith(base)]
        assert seeded[-1] == base + "/.cbs-complete"
        stray = runtime + "/models--"
        assert not any(p.startswith(stray) for p in [*cloud.files, *cloud.folders])
        assert len(client.app.state.store.results(recording["id"])) == 2


def test_v85_missing_local_speaker_model_explains_next_step(
    client, cloud, connection, monkeypatch, tmp_path
):
    with client.app.state.store.library_lock():
        store, credentials = client.app.state.store, client.app.state.credentials
        runtime_ready(cloud)
        cloud.cache["models"]["speakers"] = False
        monkeypatch.setattr(api, "_local_hub_cache", lambda: tmp_path)
        # Metadata acceptance is harmless; background preflight still refuses before allocation.
        response, recording, _ = enqueue(client, align=False, diarize=True)
        assert response.status_code == 202, response.text
        [failed] = inspect_and_materialize(store, credentials, [response.json()["id"]])
        assert failed["state"] == "failed"
        assert failed["params"]["input_intent"]["error"]["code"] == "needs_confirmation"
        preparation = input_admission.projection(store, failed["id"])
        assert preparation["can_prepare"] and not preparation["can_retry"]
        assert preparation["prepare_preview"]["count"] == 1
        # The lower-level curated explanation remains precise while the durable intent is redacted.
        options = EnhancementOptions(execution="ucloud", align=False, diarize=True)
        with pytest.raises(ValueError, match="Hugging Face read token") as error:
            api.freeze_profile(
                store,
                credentials,
                providers.profile(store, connection["id"]),
                [recording["project_id"]],
                options,
            )
        assert "run on UCloud" in str(error.value)
        assert not cloud.submissions and not cloud.uploaded
        # A marker alone is not a copy (B127); every pinned file must be there too.
        synthetic_speakers(monkeypatch)
        settings = providers.Settings.model_validate(
            providers.profile(store, connection["id"])["settings"]
        )
        marker = api.speaker_marker(settings)
        cloud.files[marker] = b"done"
        parent = PurePosixPath(marker).parent
        while str(parent) not in cloud.folders:
            cloud.folders.add(str(parent))
            parent = parent.parent
        response, _, _ = enqueue(client, align=False, diarize=True)
        assert response.status_code == 202, response.text
        [failed] = inspect_and_materialize(store, credentials, [response.json()["id"]])
        assert failed["state"] == "failed"
        assert failed["params"]["input_intent"]["error"]["code"] == "needs_confirmation"
        assert not cloud.submissions and not cloud.uploaded
        # A completed remote copy is enough without a local cache or token.
        runtime_has_models(cloud, "speakers")
        response, _, _ = enqueue(client, align=False, diarize=True)
        assert response.status_code == 202, response.text
        [ready] = inspect_and_materialize(store, credentials, [response.json()["id"]])
        assert ready["state"] == "queued" and ready["params"]["enhancement"]["diarize"] is True
        assert not cloud.submissions and not cloud.uploaded


def test_marked_but_incomplete_runtime_copy_is_repaired_before_any_gpu_job(
    client, cloud, connection, monkeypatch, tmp_path
):
    """B127: an earlier copy wrote the marker first and lost a file; an upload also drops."""
    with client.app.state.store.library_lock():
        monkeypatch.setattr(api, "SEED_RETRY_SECONDS", 0)
        files = {
            "config.yaml": b"pipeline: synthetic",
            "plda/plda.npz": b"plda",
            "plda/xvec_transform.npz": b"xvec",
        }
        write = synthetic_speakers(monkeypatch, files)
        write(tmp_path)
        monkeypatch.setattr(api, "_local_hub_cache", lambda: tmp_path)
        runtime = runtime_ready(cloud)
        cloud.cache["models"]["speakers"] = False
        repo, revision = MODELS["speakers"]
        base = runtime + "/hf/hub/models--" + repo.replace("/", "--") + "/snapshots/" + revision
        cloud.folders |= {
            runtime + "/hf",
            runtime + "/hf/hub",
            runtime + "/hf/hub/models--" + repo.replace("/", "--"),
            runtime + "/hf/hub/models--" + repo.replace("/", "--") + "/snapshots",
            base,
            base + "/plda",
        }
        cloud.files[base + "/.cbs-complete"] = b"done"
        cloud.files[base + "/config.yaml"] = b"pipeline: synthetic"
        cloud.files[base + "/plda/plda.npz"] = b"plda"
        cloud.fail_uploads = {"/plda/xvec_transform.npz": 1}
        response, recording, _ = enqueue(client, align=False, diarize=True)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        assert response.status_code == 202, response.text
        client.app.state.queue.process(response.json()["id"])
        seeded = [t for t in cloud.uploaded if t.startswith(base)]
        # Only the missing file is copied (retried once), then the marker; all before the GPU job.
        assert seeded == [base + "/plda/xvec_transform.npz"] * 2 + [base + "/.cbs-complete"]
        assert cloud.files[base + "/plda/xvec_transform.npz"] == b"xvec"
        assert cloud.speakers_at_submit is True and len(cloud.submissions) == 1
        assert len(client.app.state.store.results(recording["id"])) == 2


def test_speaker_copy_that_keeps_failing_starts_no_gpu_job(
    client, cloud, connection, monkeypatch, tmp_path
):
    with client.app.state.store.library_lock():
        monkeypatch.setattr(api, "SEED_RETRY_SECONDS", 0)
        write = synthetic_speakers(monkeypatch)
        write(tmp_path)
        monkeypatch.setattr(api, "_local_hub_cache", lambda: tmp_path)
        runtime_ready(cloud)
        cloud.cache["models"]["speakers"] = False
        cloud.fail_uploads = {"/config.yaml": api.SEED_ATTEMPTS}
        response, _, _ = enqueue(client, align=False, diarize=True)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        assert response.status_code == 202, response.text
        with pytest.raises(ValueError, match="interrupted"):
            client.app.state.queue.process(response.json()["id"])
        assert not cloud.submissions
        assert not any(t.endswith("/.cbs-complete") for t in cloud.uploaded)


@pytest.mark.parametrize("code", ["runtime-busy", "runtime-prepare", "manifest"])
def test_v85_supervisor_failures_are_curated(client, cloud, connection, code):
    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        cloud.fail_code = code
        response, _, _ = enqueue(client)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        with pytest.raises(ValueError) as failure:
            client.app.state.queue.process(response.json()["id"])
        assert str(failure.value) == api.FAILURES[code]
        if code == "runtime-busy":
            assert "may be in use or have a leftover preparation lock" in str(failure.value)
            assert "Check Activity" in str(failure.value)


def test_v85_uncertain_submission_is_never_repeated(client, cloud, connection):
    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        cloud.submission_error = httpx.ReadTimeout("private upstream detail")
        response, _, _ = enqueue(client)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        job = _admitted[0]
        queue = client.app.state.queue
        with pytest.raises(ValueError):
            queue.process(job["id"])
        cloud.submission_error = None
        with pytest.raises(ValueError, match="uncertain"):
            queue.process(job["id"])
        assert not cloud.submissions
        listed = next(j for j in client.get("/api/jobs").json() if j["id"] == job["id"])
        assert listed["ucloud_server"] == "https://cloud.invalid"


def test_v85_cancellation_terminates_only_the_owned_job(client, cloud, connection):
    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        response, _, _ = enqueue(client)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        job = _admitted[0]
        queue = client.app.state.queue
        original = cloud.step

        def cancel_when_ready():
            original()
            if cloud.phase() == "ready":
                queue.cancel_event.set()

        cloud.step = cancel_when_ready
        with pytest.raises(Cancelled):
            queue.process(job["id"])
        assert cloud.terminations == ["remote-enhancement"]
        queue.cancel_event.clear()


def test_v85_protected_projects_require_the_connection_approval(client, cloud, connection):
    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        store = client.app.state.store
        approve(client, [])
        response, _, _ = enqueue(client, classification="protected")
        assert response.status_code == 403 and "protected" in response.json()["detail"]
        assert not store.all("SELECT id FROM jobs WHERE kind='enhance'")
        approve(client, ["protected"])
        response, _, _ = enqueue(client, classification="protected")
        assert response.status_code == 202, response.text
        [job] = inspect_and_materialize(
            store, client.app.state.credentials, [response.json()["id"]]
        )
        assert job["state"] == "queued" and "input_intent" not in job["params"]
        approve(client, [])
        with pytest.raises(ValueError, match="protected"):
            client.app.state.queue.process(job["id"])
        assert not cloud.submissions and not cloud.uploaded


def test_v85_per_run_downloads_are_rejected_for_ucloud(client, cloud, connection):
    with client.app.state.store.library_lock():
        response, recording, _ = enqueue(client, allow_downloads=True)
        assert response.status_code == 202, response.text
        [failed] = inspect_and_materialize(
            client.app.state.store, client.app.state.credentials, [response.json()["id"]]
        )
        assert failed["state"] == "failed"
        assert failed["params"]["input_intent"]["error"]["code"] == "preparation_failed"
        with pytest.raises(ValueError, match="prepared runtime"):
            api.freeze(
                client.app.state.store,
                client.app.state.credentials,
                [recording["project_id"]],
                EnhancementOptions(execution="ucloud", allow_downloads=True),
            )
        assert not cloud.submissions and not cloud.uploaded


def test_v85_cleanup_retry_for_api_enhancement(client, cloud, connection, monkeypatch):
    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        response, recording, _ = enqueue(client)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        job = _admitted[0]
        store = client.app.state.store
        original = cloud.handle

        def refuse_trash(request):
            if request.url.path == "/api/files/trash":
                return httpx.Response(403, text=SECRET)
            return original(request)

        cloud.handle = refuse_trash
        client.app.state.queue.process(job["id"])
        store.update_job(job["id"], state="completed")
        run = store.job(job["id"])["params"]["ucloud_run"]
        assert run["cleanup_state"] == "pending" and "HTTP 403" in run["cleanup_error"]
        cloud.handle = original
        response = client.post(f"/api/jobs/{job['id']}/cleanup-ucloud")
        assert response.json() == {"cleanup_state": "trashed"}


def test_worker_cache_status_reports_pinned_snapshots_only(tmp_path, monkeypatch):
    def missing_tokenizer(_):
        raise LookupError("synthetic uncached tokenizer")

    # This unit case checks pinned snapshot identity. Importing NLTK's unrelated optional
    # numeric stack made one Windows run take 18 minutes; tokenizer lookup is isolated here.
    monkeypatch.setitem(
        sys.modules, "nltk", SimpleNamespace(data=SimpleNamespace(find=missing_tokenizer))
    )
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
    repo, revision = MODELS["en"]
    folder = tmp_path / "hub" / ("models--" + repo.replace("/", "--")) / "snapshots" / revision
    folder.mkdir(parents=True)
    (folder / "config.json").write_text("{}", encoding="utf-8")
    wrong = tmp_path / "hub" / ("models--" + MODELS["da"][0].replace("/", "--")) / "snapshots"
    (wrong / "other-revision").mkdir(parents=True)
    status = cache_status()
    assert status["models"]["en"] and not status["models"]["da"]
    assert not status["models"]["speakers"]
    assert not status["punkt_tab"]


def test_v85_status_is_offline_and_runtime_check_is_read_only(client, cloud, credentials):
    status = client.get("/api/enhancement-api/status").json()
    assert not status["connected"] and "Settings" in status["message"]
    providers.save_profile(
        client.app.state.store,
        credentials,
        "UCloud",
        providers.Settings(
            provider_type="ucloud", base_url="https://cloud.invalid", ucloud_transcriber=SETUP
        ),
        api_key=SECRET,
        remember=False,
    )
    status = client.get("/api/enhancement-api/status").json()
    assert status["connected"] and status["storage_path"] == "/drive/cbs"
    assert status["runtime_id"] == api.runtime_id() and SECRET not in json.dumps(status)
    assert client.post("/api/enhancement-api/check", json={}).json()["prepared"] is False
    runtime = runtime_ready(cloud)
    stamp = cloud.files.pop(runtime + "/stamp.json")
    # A folder without its completion stamp (interrupted preparation) is not prepared.
    assert client.post("/api/enhancement-api/check", json={}).json()["prepared"] is False
    cloud.files[runtime + "/stamp.json"] = stamp
    assert client.post("/api/enhancement-api/check", json={}).json()["prepared"] is True
    assert not cloud.submissions and not cloud.uploaded


def test_v85_stopped_job_is_not_reused_and_inputs_never_reach_it(client, cloud, connection):
    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        cloud.cache["models"]["da"] = False
        response, _, _ = enqueue(client)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        job = _admitted[0]
        queue = client.app.state.queue
        with pytest.raises(ValueError, match="lacks cached models"):
            queue.process(job["id"])
        cloud.cache["models"]["da"] = True
        with pytest.raises(ValueError, match="was stopped"):
            queue.process(job["id"])
        run = SETUP["storage_path"] + "/cbs-enhancement-" + job["id"]
        assert run + "/audio.wav" not in cloud.uploaded and len(cloud.submissions) == 1


def test_v85_revoked_approval_after_submission_stops_job_before_private_upload(
    client, cloud, connection
):
    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        response, _, _ = enqueue(client, classification="protected")
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        job = _admitted[0]
        original = cloud.step

        def revoke_when_ready():
            original()
            if cloud.phase() == "ready":
                approve(client, [])

        cloud.step = revoke_when_ready
        with pytest.raises(ValueError, match="protected"):
            client.app.state.queue.process(job["id"])
        run = SETUP["storage_path"] + "/cbs-enhancement-" + job["id"]
        assert run + "/audio.wav" not in cloud.uploaded
        assert cloud.terminations == ["remote-enhancement"]


def test_v85_transient_reads_are_tolerated_without_stopping(client, cloud, connection):
    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        response, recording, _ = enqueue(client)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        original = cloud.handle
        failures = {"left": 3}

        def flaky(request):
            if request.url.path == "/api/files/download" and failures["left"]:
                failures["left"] -= 1
                return httpx.Response(503)
            return original(request)

        cloud.handle = flaky
        client.app.state.queue.process(response.json()["id"])
        assert len(client.app.state.store.results(recording["id"])) == 2
        assert not failures["left"]


def test_v85_lost_connection_keeps_the_owned_job_for_reconnect(
    client, cloud, connection, monkeypatch
):
    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        monkeypatch.setattr(api, "MAX_TRANSIENT", 1)
        response, recording, _ = enqueue(client)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        job = _admitted[0]
        original = cloud.handle
        down = {"value": False}

        def outage(request):
            if down["value"] and request.url.host == "cloud.invalid":
                return httpx.Response(503)
            reply = original(request)
            if request.url.path == "/api/jobs" and request.method == "POST":
                down["value"] = True
            return reply

        cloud.handle = outage
        with pytest.raises(api.Transient):
            client.app.state.queue.process(job["id"])
        assert not cloud.terminations
        down["value"] = False
        client.app.state.queue.process(job["id"])
        assert len(cloud.submissions) == 1
        assert len(client.app.state.store.results(recording["id"])) == 2


def test_v85_crash_after_local_outputs_finishes_and_cleans_on_retry(
    client, cloud, connection, monkeypatch
):
    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        response, recording, _ = enqueue(client)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        job = _admitted[0]
        store, queue = client.app.state.store, client.app.state.queue
        real_stop = api.stop
        crashed = {"done": False}

        def crash_once(client_, run, checkpoint):
            if not crashed["done"]:
                crashed["done"] = True
                raise OSError("synthetic crash after outputs were saved locally")
            return real_stop(client_, run, checkpoint)

        monkeypatch.setattr(api, "stop", crash_once)
        with pytest.raises(OSError):
            queue.process(job["id"])
        assert len(store.results(recording["id"])) == 1
        queue.process(job["id"])
        assert len(store.results(recording["id"])) == 2
        run = store.job(job["id"])["params"]["ucloud_run"]
        assert run["cleanup_state"] == "trashed" and run["termination_requested"]
        assert len(cloud.submissions) == 1


def whisperx_profile(client, model="whisperx/large-v3"):
    """WhisperX models appear as ordinary profiles of the UCloud connection (T70)."""
    store = client.app.state.store
    for row in store.all("SELECT id FROM providers"):
        current = providers.profile(store, row["id"])
        if current["settings"]["model"] == model:
            return current
    raise AssertionError("The UCloud connection has no WhisperX profile.")


def runtime_has_models(cloud, *keys):
    folder = SETUP["storage_path"] + "/cbs-runtime/" + api.runtime_id()
    for key in keys:
        repo, revision = MODELS[key]
        path = PurePosixPath(
            folder + "/hf/hub/models--" + repo.replace("/", "--") + "/snapshots/" + revision
        )
        cloud.folders |= {str(parent) for parent in path.parents} | {str(path)}
        if key == "speakers":
            for relative, (size, _) in api.SPEAKER_FILES.items():
                target = path / relative
                cloud.folders |= {str(parent) for parent in target.parents}
                cloud.files[str(target)] = b"x" * size
            cloud.files[str(path / api.SPEAKERS_COMPLETE)] = b"done"


def transcription_request(client, prepare=False, **features):
    store = client.app.state.store
    project = store.create_project("WhisperX transcription")
    recording = store.create_recording(project["id"], "synthetic.wav")
    write_original_wav(store, recording)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    profile = whisperx_profile(client)
    # Runtime profiles are parked by default; these explicit full-profile tests opt in.
    settings = providers.Settings.model_validate(profile["settings"])
    settings.enabled = True
    providers.save_profile(
        store,
        client.app.state.credentials,
        profile["name"],
        settings,
        identifier=profile["id"],
    )
    response = client.post(
        "/api/transcriptions",
        json={
            "recording_ids": [recording["id"]],
            "provider_ids": [profile["id"]],
            "requested_features": {"word_timing": True, "speaker_labels": False, **features},
            "prepare_runtime": prepare,
        },
    )
    return response, recording, profile


def test_t70_whisperx_is_a_ucloud_model_that_asks_before_preparing(client, cloud, connection):
    with client.app.state.store.library_lock():
        store = client.app.state.store
        profile = whisperx_profile(client)
        assert profile["name"] == "UCloud WhisperX large-v3 (CBS Runtime)"
        runtime_ready(cloud)  # Prepared packages, but no large-v3 weights yet.
        response, _, _ = transcription_request(client)
        assert response.status_code == 202, response.text
        [pending] = response.json()
        [failed] = inspect_and_materialize(store, client.app.state.credentials, [pending["id"]])
        assert failed["state"] == "failed"
        assert failed["params"]["input_intent"]["error"]["code"] == "needs_confirmation"
        assert input_admission.projection(store, failed["id"])["can_retry"] is False
        assert not store.all("SELECT id FROM runtime_batch_runs")
        assert not cloud.submissions and not cloud.uploaded
        # Only a new explicit request may broaden its consent; failed accepted choices stay frozen.
        response, recording, _ = transcription_request(client, prepare=True)
        assert response.status_code == 202, response.text
        [pending] = response.json()
        [job] = inspect_and_materialize(store, client.app.state.credentials, [pending["id"]])
        assert job["kind"] == "enhance" and job["params"]["ucloud_api"]["prepare_runtime"] is True
        client.app.state.queue.process(job["id"])
        folder = store.directory(recording["id"]) / "enhancements" / job["id"]
        script = (folder / "supervisor.sh").read_text(encoding="utf-8")
        assert "PREPARE=1" in script and 'MODELS="whisper-large-v3,coarse,da"' in script


def test_t70_ucloud_whisperx_transcription_appends_a_provider_result(client, cloud, connection):
    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        runtime_has_models(cloud, "whisper-large-v3", "coarse", "da")
        response, recording, profile = transcription_request(client)
        assert response.status_code == 202, response.text
        _pending_jobs = response.json()
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        assert response.status_code == 202, response.text
        [job] = _admitted
        params = job["params"]
        assert params["transcription"] is True and params["provider_id"] == profile["id"]
        assert params["ucloud_api"]["provider_id"] == profile["id"]
        assert params["enhancement"]["transcribe"] and params["enhancement"]["device"] == "cuda"
        assert params["enhancement"]["whisper_model"] == "large-v3"
        assert not params["enhancement"]["diarize"]
        store, queue = client.app.state.store, client.app.state.queue
        queue.process(job["id"])
        queue.process(job["id"])
        run = SETUP["storage_path"] + "/cbs-enhancement-" + job["id"]
        assert len(cloud.submissions) == 1 and cloud.trashed == [run]
        [result] = store.results(recording["id"])
        saved = store.result(recording["id"], result["id"])
        assert (
            saved["source"]["kind"] == "provider" and saved["source"]["provider_type"] == "whisperx"
        )
        assert saved["source"]["provider_id"] == profile["id"]
        assert saved["source"]["model"] == "large-v3" and saved["source"]["transport"] == "api"
        revisions = saved["source"]["model_revisions"]
        assert revisions["whisper-large-v3"]["repo"] == "Systran/faster-whisper-large-v3"
        assert saved["segments"][0]["text"] == "Hej København."
        assert saved["segments"][0]["word_timing"] is True
        assert (
            store.result_versions(recording["id"], result["id"])[0]["action"] == "provider_result"
        )
        hints = store.result_hints(recording["id"], result["id"])
        assert hints["passages"]["0"]["avg_logprob"] == -0.31
        assert store.recording(recording["id"])["active_transcript_id"] == result["id"]


def test_t70_speaker_labels_run_in_the_same_whisperx_job(client, cloud, connection, monkeypatch):
    with client.app.state.store.library_lock():
        synthetic_speakers(monkeypatch)
        runtime_ready(cloud)
        runtime_has_models(cloud, "whisper-large-v3", "coarse", "da", "speakers")
        response, recording, _ = transcription_request(client, speaker_labels=True)
        assert response.status_code == 202, response.text
        _pending_jobs = response.json()
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        assert response.status_code == 202, response.text
        [job] = _admitted
        assert job["params"]["enhancement"]["diarize"] is True
        assert "followup" not in job["params"]
        client.app.state.queue.process(job["id"])
        [result] = client.app.state.store.results(recording["id"])
        saved = client.app.state.store.result(recording["id"], result["id"])
        assert saved["source"]["capability"] == "words_speakers"
        assert saved["segments"][0]["speaker"] != "UNKNOWN"


def test_t70_runtime_without_the_whisper_model_stops_before_upload(client, cloud, connection):
    with client.app.state.store.library_lock():
        runtime_ready(cloud)
        runtime_has_models(cloud, "whisper-large-v3", "coarse", "da")
        cloud.cache["models"]["whisper-large-v3"] = False  # Folder present, copy incomplete.
        response, recording, _ = transcription_request(client)
        assert response.status_code == 202, response.text
        _pending_jobs = response.json()
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        [job] = _admitted
        with pytest.raises(ValueError, match=r"lacks cached models \(whisper-large-v3\)"):
            client.app.state.queue.process(job["id"])
        assert not any(name.endswith("/audio.wav") for name in cloud.uploaded)
        assert client.app.state.store.results(recording["id"]) == []


def test_t70_explicit_preparation_fetches_only_public_job_models(tmp_path):
    from cbs_transcriber.enhancement import EnhancementOptions

    options = EnhancementOptions(
        execution="ucloud", transcribe=True, whisper_model="large-v3-turbo", diarize=True
    )
    assert api.required_models(options) == ["whisper-large-v3-turbo", "coarse", "da", "speakers"]
    assert api.prefetch_models(options) == ["whisper-large-v3-turbo", "coarse", "da"]
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="large-v3",
        ucloud_transcriber=SETUP,
    )
    script = api.supervisor_script(settings, "abc123", True, api.prefetch_models(options))
    assert 'MODELS="whisper-large-v3-turbo,coarse,da"' in script
    assert "fetch_models" in script and "speakers" not in script.split("MODELS=")[1][:40]
    with pytest.raises(ValueError, match="Unexpected model"):
        api.supervisor_script(settings, "abc123", True, ["$(rm -rf /)"])


def test_t71_ucloud_retranscription_runs_one_job_for_the_selected_passages(
    client, cloud, connection
):
    runtime_ready(cloud)
    runtime_has_models(cloud, "whisper-large-v3-turbo", "coarse", "da")
    store = client.app.state.store
    project = store.create_project("Re-transcribe on UCloud")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Hej Kbh.", "start": 0, "end": 3}]),
        {},
        "Reviewed",
        {},
    )
    response = client.post(
        f"/api/recordings/{recording['id']}/retranscriptions",
        json={
            "result_id": result["id"],
            "revision": 1,
            "segment_ids": ["0"],
            "execution": "ucloud",
            "whisper_model": "large-v3-turbo",
        },
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["params"]["enhancement"]["device"] == "cuda"
    client.app.state.queue.process(job["id"])
    assert len(cloud.submissions) == 1
    new = store.results(recording["id"])[-1]
    saved = store.result(recording["id"], new["id"])
    assert saved["segments"][0]["text"] == "Hej København."
    assert saved["segments"][0]["word_timing"] is True
    assert saved["source"]["operation"] == "retranscription"
    assert saved["source"]["transport"] == "api"
    assert store.result(recording["id"], result["id"])["segments"][0]["text"] == "Hej Kbh."


def test_speaker_model_is_fetched_here_and_copied_with_explicit_preparation(
    client, cloud, connection, monkeypatch, tmp_path, credentials
):
    """Speakers run on UCloud: no local detection run is needed first (ADR 0055)."""
    with client.app.state.store.library_lock():
        runtime = runtime_ready(cloud)
        cloud.cache["models"]["speakers"] = False
        monkeypatch.setattr(api, "_local_hub_cache", lambda: tmp_path)
        credentials.set(api.HF_SCOPE, "synthetic-hf-token", False)
        from cbs_transcriber import huggingface_access

        monkeypatch.setattr(
            huggingface_access, "check_token", lambda token: {"speaker_model_access": "allowed"}
        )
        # Without preparation consent, accept metadata only: no fetch or inference allocation.
        refused, recording, _ = enqueue(client, align=False, diarize=True)
        assert refused.status_code == 202, refused.text
        _pending_jobs = [refused.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        assert _admitted[0]["state"] == "failed"
        assert _admitted[0]["params"]["input_intent"]["error"]["code"] == "needs_confirmation"
        preparation = input_admission.projection(client.app.state.store, _admitted[0]["id"])
        assert preparation["can_prepare"] and not preparation["can_retry"]
        with pytest.raises(ValueError, match="Prepare the UCloud runtime"):
            api.freeze_profile(
                client.app.state.store,
                credentials,
                providers.profile(client.app.state.store, connection["id"]),
                [recording["project_id"]],
                EnhancementOptions(execution="ucloud", align=False, diarize=True),
            )
        assert not cloud.submissions and not cloud.uploaded
        fetched = []
        write = synthetic_speakers(monkeypatch)

        def fetch(token):
            fetched.append(token)
            return write(tmp_path)

        monkeypatch.setattr(api, "fetch_speaker_model", fetch)
        response, recording, _ = enqueue(client, align=False, diarize=True, prepare_runtime=True)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        assert response.status_code == 202, response.text
        client.app.state.queue.process(response.json()["id"])
        assert fetched == ["synthetic-hf-token"]
        assert cloud.speakers_at_submit is True
        repo, revision = MODELS["speakers"]
        base = runtime + "/hf/hub/models--" + repo.replace("/", "--") + "/snapshots/" + revision
        assert cloud.files[base + "/config.yaml"] == b"pipeline: synthetic"
        # The token is used only on this computer; it never reaches UCloud.
        assert not any(b"synthetic-hf-token" in data for data in cloud.files.values())
        assert len(client.app.state.store.results(recording["id"])) == 2


def test_denied_gated_speaker_model_is_refused_before_any_gpu_job(
    client, cloud, connection, monkeypatch, tmp_path, credentials
):
    """A valid token without accepted pyannote terms must not start a UCloud job (B125)."""
    with client.app.state.store.library_lock():
        from cbs_transcriber import huggingface_access

        runtime_ready(cloud)
        cloud.cache["models"]["speakers"] = False
        monkeypatch.setattr(api, "_local_hub_cache", lambda: tmp_path)
        credentials.set(api.HF_SCOPE, "synthetic-hf-token", False)
        monkeypatch.setattr(
            huggingface_access, "check_token", lambda token: {"speaker_model_access": "denied"}
        )
        response, recording, _ = enqueue(client, align=False, diarize=True, prepare_runtime=True)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        assert _admitted[0]["state"] == "failed"
        assert _admitted[0]["params"]["input_intent"]["error"]["code"] == "preparation_failed"
        with pytest.raises(ValueError, match="accept its terms"):
            api.freeze_profile(
                client.app.state.store,
                credentials,
                providers.profile(client.app.state.store, connection["id"]),
                [recording["project_id"]],
                EnhancementOptions(
                    execution="ucloud", align=False, diarize=True, prepare_runtime=True
                ),
            )
        assert not cloud.submissions and not cloud.uploaded


def test_refused_download_stops_before_any_gpu_job(
    client, cloud, connection, monkeypatch, tmp_path, credentials
):
    """Local speaker preparation runs before allocation, so a refusal costs no GPU (ADR 0057)."""
    with client.app.state.store.library_lock():
        from cbs_transcriber import huggingface_access

        runtime_ready(cloud)
        cloud.cache["models"]["speakers"] = False
        monkeypatch.setattr(api, "_local_hub_cache", lambda: tmp_path)
        credentials.set(api.HF_SCOPE, "synthetic-hf-token", False)
        monkeypatch.setattr(
            huggingface_access, "check_token", lambda token: {"speaker_model_access": "allowed"}
        )

        class GatedRepoError(OSError):  # Hub errors are OSError subclasses.
            pass

        def refuse(token):
            raise GatedRepoError("403")

        monkeypatch.setattr(api, "fetch_speaker_model", refuse)
        response, recording, _ = enqueue(client, align=False, diarize=True, prepare_runtime=True)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        assert response.status_code == 202, response.text
        with pytest.raises(ValueError, match="accept its terms"):
            client.app.state.queue.process(response.json()["id"])
        assert not cloud.submissions and not cloud.uploaded


def test_locked_file_during_speaker_download_is_retried_then_verified(
    client, monkeypatch, credentials, isolated_speaker_model
):
    """Check token prepares the speaker model; a briefly locked file is retried (ADR 0057)."""
    import huggingface_hub

    from cbs_transcriber import huggingface_access

    write = synthetic_speakers(monkeypatch)
    calls = []

    def download(repo, **kwargs):
        calls.append(kwargs)
        assert kwargs["token"] == "hf_SYNTHETIC" and kwargs["allow_patterns"] == ["config.yaml"]
        if len(calls) == 1:
            raise PermissionError(13, "The process cannot access the file")
        snapshot = api.snapshot_folder("speakers", Path(kwargs["cache_dir"]))
        if not (snapshot / "config.yaml").exists():
            write(Path(kwargs["cache_dir"]))
        return str(snapshot)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    monkeypatch.setattr(
        huggingface_access,
        "check_token",
        lambda token: (
            huggingface_access.token_state(token)
            | {"token_status": "valid", "speaker_model_access": "allowed"}
        ),
    )
    assert client.get("/api/huggingface-settings").json()["speaker_model_local"] == "missing"
    credentials.set(api.HF_SCOPE, "hf_SYNTHETIC", False)
    checked = client.post("/api/huggingface-settings/check", json={}).json()
    assert len(calls) == 2
    assert checked["speaker_model_local"] == "verified" and not checked["speaker_model_error"]
    assert client.get("/api/huggingface-settings").json()["speaker_model_local"] == "verified"
    assert "hf_SYNTHETIC" not in json.dumps(checked)
    # A damaged file is never used or copied: it is removed and fetched again (the Hub alone
    # would keep the damaged copy).
    snapshot = api.snapshot_folder("speakers", isolated_speaker_model)
    (snapshot / "config.yaml").write_bytes(b"pipeline: damaged!!")
    with pytest.raises(api.RuntimeNotReady, match="incomplete or damaged"):
        api.local_speaker_files()
    assert api.local_speaker_status("hf_SYNTHETIC")["speaker_model_local"] == "verified"
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("error", "attempts", "reason"),
    [
        ("refused", 1, "accept its terms"),
        ("token", 1, "rejected the saved token"),
        ("rate", api.FETCH_ATTEMPTS, "internet connection"),
        ("network", api.FETCH_ATTEMPTS, "internet connection"),
        ("disk", api.FETCH_ATTEMPTS, "No space left"),
    ],
)
def test_speaker_download_failures_are_named_and_only_transient_ones_retried(
    client, monkeypatch, credentials, error, attempts, reason
):
    import huggingface_hub
    import requests

    from cbs_transcriber import huggingface_access

    synthetic_speakers(monkeypatch)
    calls = []

    class Refused(OSError):
        response = type("Response", (), {"status_code": 403})()

    class Rejected(OSError):
        response = type("Response", (), {"status_code": 401})()

    limited = requests.HTTPError("429")
    limited.response = type("Response", (), {"status_code": 429})()

    def download(repo, **kwargs):
        calls.append(repo)
        raise {
            "refused": Refused("403"),
            "token": Rejected("401"),
            "rate": limited,
            "network": requests.ConnectionError("offline"),
            "disk": OSError(28, "No space left on device"),
        }[error]

    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    monkeypatch.setattr(
        huggingface_access,
        "check_token",
        lambda token: (
            huggingface_access.token_state(token)
            | {"token_status": "valid", "speaker_model_access": "allowed"}
        ),
    )
    credentials.set(api.HF_SCOPE, "hf_SYNTHETIC", False)
    checked = client.post("/api/huggingface-settings/check", json={}).json()
    assert len(calls) == attempts
    assert checked["speaker_model_local"] == "failed" and reason in checked["speaker_model_error"]


def test_rejected_token_is_named_before_any_download(monkeypatch, credentials):
    import huggingface_hub

    from cbs_transcriber import huggingface_access

    synthetic_speakers(monkeypatch)
    monkeypatch.setattr(
        huggingface_access,
        "check_token",
        lambda token: huggingface_access.token_state(token) | {"token_status": "invalid"},
    )
    monkeypatch.setattr(huggingface_hub, "snapshot_download", pytest.fail)
    with pytest.raises(api.RuntimeNotReady, match="rejected the saved token"):
        api.prepare_local_speakers("hf_REVOKED")


def test_refused_upload_session_is_not_retried_and_keeps_its_reason(
    client, cloud, connection, monkeypatch, tmp_path
):
    with client.app.state.store.library_lock():
        write = synthetic_speakers(monkeypatch)
        write(tmp_path)
        monkeypatch.setattr(api, "_local_hub_cache", lambda: tmp_path)
        runtime_ready(cloud)
        cloud.cache["models"]["speakers"] = False
        cloud.refuse_sessions = {"/config.yaml"}
        response, _, _ = enqueue(client, align=False, diarize=True)
        assert response.status_code == 202, response.text
        _pending_jobs = [response.json()]
        assert all("input_intent" in pending["params"] for pending in _pending_jobs)
        _admitted = inspect_and_materialize(
            client.app.state.store,
            client.app.state.credentials,
            [pending["id"] for pending in _pending_jobs],
        )
        assert response.status_code == 202, response.text
        with pytest.raises(ValueError, match="HTTP 403"):
            client.app.state.queue.process(response.json()["id"])
        assert cloud.refused == 1 and not cloud.submissions
