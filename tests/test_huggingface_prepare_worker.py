"""Standalone helper protocol/origin tests; all model and metadata operations are mocked."""

import builtins
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement_api
from cbs_transcriber import huggingface_prepare_worker as subject

SECRET = "hf_SYNTHETIC_PIPE_ONLY_SECRET"


def request(mode="inspect", **changes):
    value = {
        "protocol": subject.PROTOCOL,
        "mode": mode,
        "nonce": "a" * 32,
        "package_path": str(subject.HERE),
        "helper_sha256": subject._digest(Path(subject.__file__).resolve())[0],
        "worker_sha256": subject._digest(subject.HERE / "enhancement_worker.py")[0],
        "device": "cpu",
    }
    if mode == "speakers":
        value["token"] = SECRET
    return value | changes


@pytest.fixture
def public_worker(tmp_path, monkeypatch):
    calls = []
    caches = {"models": dict.fromkeys(subject.PUBLIC_MODELS, True), "punkt_tab": True}

    def prefetch(keys):
        calls.append(keys)
        assert os.environ["NLTK_DATA"] == str(tmp_path / "existing-nltk")
        assert os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"
        return caches

    worker = SimpleNamespace(
        WHISPERX_VERSION="synthetic",
        doctor=lambda **_: {"ready": True, "version": "synthetic", "gpu": {"available": True}},
        hub_cache=lambda: tmp_path / "existing-hf",
        prefetch=prefetch,
    )
    monkeypatch.setitem(
        sys.modules,
        "nltk",
        SimpleNamespace(data=SimpleNamespace(path=[str(tmp_path / "existing-nltk")])),
    )
    monkeypatch.setattr(subject, "_public_worker", lambda _: worker)
    monkeypatch.setenv("NLTK_DATA", str(tmp_path / "existing-nltk"))
    monkeypatch.setenv("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    return worker, calls, caches


def test_public_helper_uses_selected_cache_targets_without_importing_the_app(
    public_worker, monkeypatch
):
    worker, calls, _ = public_worker
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert not name.startswith("cbs_transcriber")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    inspected = subject.execute(request())
    assert inspected["status"] == "ready" and calls == []
    prepared = subject.execute(request("public", cache_targets=inspected["cache_targets"]))
    assert prepared["status"] == "ready"
    assert prepared["cache_targets"] == inspected["cache_targets"]
    assert calls == [()]
    assert SECRET not in json.dumps(prepared)
    assert set(prepared) == {
        "protocol",
        "nonce",
        "helper_sha256",
        "worker_sha256",
        "status",
        "cache_targets",
    }


@pytest.mark.parametrize(
    "state,code",
    [
        ({"unsupported_platform": True}, "unsupported"),
        ({"ready": False}, "runtime_missing"),
        ({"gpu": {"available": False}}, "cuda_unavailable"),
    ],
)
def test_public_helper_refuses_unsupported_runtime_or_explicit_unavailable_cuda(
    public_worker, state, code
):
    worker, calls, _ = public_worker
    ready = {"ready": True, "version": "synthetic", "gpu": {"available": True}}
    worker.doctor = lambda **_: ready | state
    result = subject.execute(request("public", device="cuda"))
    assert result["status"] == "unavailable" and result["code"] == code
    assert calls == []


def test_cache_target_change_or_incomplete_public_prefetch_never_claims_prepared(
    public_worker, tmp_path
):
    _, calls, caches = public_worker
    wrong = {"hf": str(tmp_path / "different-hf"), "nltk": str(tmp_path / "existing-nltk")}
    result = subject.execute(request("public", cache_targets=wrong))
    assert result["code"] == "cache_changed" and calls == []
    caches["models"]["da"] = False
    result = subject.execute(request("public"))
    assert result["status"] == "failed" and result["code"] == "incomplete"


def test_speakers_use_owned_app_source_and_existing_verified_cache_primitive(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(
        enhancement_api, "prepare_local_speakers", lambda token: calls.append(token)
    )
    result = subject.execute(request("speakers"))
    assert calls == [SECRET]
    assert result["status"] == "ready" and result["speaker_model_local"] == "verified"
    assert SECRET not in json.dumps(result)


def test_wrong_package_or_source_digest_and_public_secret_input_are_refused(tmp_path):
    for value in (
        request(package_path=str(tmp_path)),
        request(helper_sha256="0" * 64),
        request(worker_sha256="0" * 64),
        request("speakers", worker_sha256="0" * 64),
        request(token=SECRET),
        request("speakers", token="bad\nsecret"),
        request("speakers", token="x" * 4097),
    ):
        with pytest.raises(subject.Refused):
            subject.execute(value)
    with pytest.raises(subject.Refused, match="source"):
        subject._public_worker("0" * 64)


def test_wrong_imported_app_origin_is_refused_before_speaker_download(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(
        sys.modules, "cbs_transcriber", SimpleNamespace(__file__=str(tmp_path / "__init__.py"))
    )
    with pytest.raises(subject.Refused, match="source"):
        subject.execute(request("speakers"))


def test_public_source_loads_only_owned_standalone_bytes_and_has_explicit_no_auth_prefetch():
    path = subject.HERE / "enhancement_worker.py"
    digest, raw = subject._digest(path)
    worker = subject._public_worker(digest)
    assert Path(worker.__file__).resolve() == path
    assert worker.MODELS["speakers"][0] == "pyannote/speaker-diarization-community-1"
    assert (
        "token=False" in raw.decode("utf-8").split("def prefetch(", 1)[1].split("def token(", 1)[0]
    )
    assert hashlib.sha256(raw).hexdigest() == digest


@pytest.mark.parametrize(
    "payload", [b"{malformed " + SECRET.encode(), b"x" * (subject.MAX_REQUEST + 1)]
)
def test_standalone_bad_input_is_bounded_redacted_and_never_imports_preparation(payload):
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(Path(subject.__file__).resolve())],
        input=payload,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0 and result.stderr == b""
    assert len(result.stdout) <= subject.MAX_REPLY
    data = json.loads(result.stdout)
    assert data["status"] == "failed"
    assert SECRET.encode() not in result.stdout
