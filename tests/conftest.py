import io
import json
import os
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cbs_transcriber.app import create_app

# Test apps never run the background check of this computer's runtime and GPU.
os.environ.setdefault("CBS_TRANSCRIBER_NO_STARTUP_CHECK", "1")
# Test apps never re-evaluate retained UCloud obligations in the background (ADR 0101).
os.environ.setdefault("CBS_TRANSCRIBER_NO_REMOTE_RELEASE", "1")
# Existing suites run the full release profile, also on a release branch whose packaged
# profile is mvp (ADR 0145). An explicit CBS_TRANSCRIBER_PROFILE=mvp run is honoured.
os.environ.setdefault("CBS_TRANSCRIBER_PROFILE", "full")


@pytest.fixture(autouse=True)
def loopback_testclient(monkeypatch):
    """TestClient's convenience hostname must not expand the production host allowlist."""
    initialize = TestClient.__init__

    def local_client(self, *args, **kwargs):
        if len(args) < 2:
            kwargs.setdefault("base_url", "http://127.0.0.1")
        initialize(self, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", local_client)


class MemoryCredentials:
    def __init__(self):
        self.keys = {}

    def get(self, base_url):
        return self.keys.get(base_url, "")

    def set(self, base_url, value, persist):
        self.keys[base_url] = value


@pytest.fixture(autouse=True)
def isolated_library_deletions(tmp_path_factory, monkeypatch):
    """Library-deletion reservations never read or write this computer's app folder."""
    from uuid import uuid4

    from cbs_transcriber import library_deletion

    records = tmp_path_factory.getbasetemp() / "library-deletions" / uuid4().hex
    monkeypatch.setattr(library_deletion, "journal_root", lambda: records)
    return records


@pytest.fixture(autouse=True)
def isolated_speaker_model(tmp_path_factory, monkeypatch):
    """Never read this computer's Hugging Face cache or download a model during tests."""
    import huggingface_hub

    from cbs_transcriber import enhancement, enhancement_api

    # This computer's cached runtime/GPU check never carries over between tests.
    monkeypatch.setattr(enhancement, "_hardware", {})
    cache = tmp_path_factory.mktemp("hf-hub")
    monkeypatch.setattr(enhancement_api, "_local_hub_cache", lambda: cache)
    monkeypatch.setattr(enhancement_api, "FETCH_RETRY_SECONDS", 0)
    monkeypatch.setattr(enhancement_api, "SEED_RETRY_SECONDS", 0)

    def refuse(*args, **kwargs):
        raise OSError("tests never download models")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", refuse)
    return cache


@pytest.fixture(autouse=True)
def unknown_runtime_readiness(monkeypatch):
    """Request-time UCloud readiness probes never reach the network in tests (ADR 0098).

    Unknown readiness keeps the accept-then-confirm fallback; consent tests set states."""
    from cbs_transcriber import enhancement_api

    monkeypatch.setattr(
        enhancement_api,
        "preparation_states",
        lambda credentials, targets, **_: dict.fromkeys(targets),
    )


@pytest.fixture
def credentials():
    return MemoryCredentials()


@pytest.fixture
def client(tmp_path, credentials):
    app = create_app(tmp_path, start_worker=False, credentials=credentials)
    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        yield client


@pytest.fixture
def wav_bytes():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 48000)
    return stream.getvalue()


@pytest.fixture
def current_transcriber_application():
    application = json.loads(
        (Path(__file__).parent / "fixtures/ucloud_transcriber_batch_1_12.json").read_text()
    )
    # Only names/types/identity above are observed. Other metadata stays synthetic.
    for parameter in application["invocation"]["parameters"]:
        parameter["optional"] = True
        if parameter["type"] == "enumeration":
            parameter["options"] = [
                {
                    "value": {"model": "large-v3", "language": "da", "outputFormat": "json"}[
                        parameter["name"]
                    ]
                }
            ]
        if parameter["name"] == "jupyterLab":
            parameter["defaultValue"] = True  # Explicitly overridden for unattended execution.
    application["invocation"]["tool"] = {
        "name": "synthetic-transcriber-tool",
        "version": "1.12",
        "tool": {"description": {"backend": "DOCKER", "supportedProviders": ["ucloud"]}},
    }
    return application


def finish_media(client, uploaded):
    queue = client.app.state.queue
    job = uploaded["job"]
    queue.store.update_job(job["id"], state="running")
    queue.process(job["id"])
    queue.store.update_job(job["id"], state="completed", progress=1)
    return queue.store.recording(uploaded["recording"]["id"])
