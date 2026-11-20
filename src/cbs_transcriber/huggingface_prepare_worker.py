"""Bounded model preparation child; credentials arrive only on ephemeral stdin.

Public preparation loads the exact sibling standalone worker without importing the app.
Speaker preparation uses the app interpreter and proves its package origin before imports.
"""

import contextlib
import hashlib
import json
import os
import re
import sys
import types
from pathlib import Path

PROTOCOL = "cbs-hf-prepare-v1"
MAX_REQUEST = 16 * 1024
MAX_REPLY = 32 * 1024
MAX_SOURCE = 4 * 1024 * 1024
HERE = Path(__file__).resolve().parent
PUBLIC_MODELS = ("coarse", "da", "en")


class Refused(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _digest(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_SOURCE + 1)
    if len(raw) > MAX_SOURCE:
        raise Refused("source")
    return hashlib.sha256(raw).hexdigest(), raw


def _request(value):
    required = {
        "protocol",
        "mode",
        "nonce",
        "package_path",
        "helper_sha256",
        "worker_sha256",
        "device",
    }
    if (
        type(value) is not dict
        or not required <= value.keys()
        or value.keys() - required - {"token", "cache_targets"}
        or value.get("protocol") != PROTOCOL
        or not isinstance(value.get("mode"), str)
        or value["mode"] not in {"inspect", "public", "speakers"}
        or not isinstance(value.get("device"), str)
        or value["device"] not in {"auto", "cpu", "cuda"}
        or not isinstance(value.get("nonce"), str)
        or re.fullmatch(r"[a-f0-9]{32}", value["nonce"]) is None
    ):
        raise Refused("input")
    for key in ("helper_sha256", "worker_sha256"):
        if not isinstance(value[key], str) or re.fullmatch(r"[a-f0-9]{64}", value[key]) is None:
            raise Refused("input")
    root = value["package_path"]
    if (
        not isinstance(root, str)
        or not 0 < len(root) <= 4096
        or not Path(root).is_absolute()
        or Path(root).resolve() != HERE
        or _digest(Path(__file__).resolve())[0] != value["helper_sha256"]
        or _digest(HERE / "enhancement_worker.py")[0] != value["worker_sha256"]
    ):
        raise Refused("source")
    token = value.get("token", "")
    if (
        not isinstance(token, str)
        or len(token) > 4096
        or any(character.isspace() or ord(character) < 32 for character in token)
        or (value["mode"] == "speakers") != bool(token)
    ):
        raise Refused("input")
    targets = value.get("cache_targets")
    if targets is not None and (
        type(targets) is not dict
        or set(targets) != {"hf", "nltk"}
        or any(
            not isinstance(path, str) or not 0 < len(path) <= 4096 or not Path(path).is_absolute()
            for path in targets.values()
        )
    ):
        raise Refused("input")
    return value


def _public_worker(expected):
    path = HERE / "enhancement_worker.py"
    digest, raw = _digest(path)
    if digest != expected:
        raise Refused("source")
    module = types.ModuleType("cbs_hf_owned_public_worker")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module


def _cache_targets(worker):
    import nltk

    if not nltk.data.path:
        raise Refused("cache_target")
    # This is the selected interpreter's existing lookup target, not a new app cache.
    return {
        "hf": str(worker.hub_cache().expanduser().absolute()),
        "nltk": str(Path(nltk.data.path[0]).expanduser().absolute()),
    }


def _public(request):
    worker = _public_worker(request["worker_sha256"])
    status = worker.doctor(hardware=request["device"] == "cuda")
    if status.get("unsupported_platform"):
        return {"status": "unavailable", "code": "unsupported"}
    if not status.get("ready") or status.get("version") != worker.WHISPERX_VERSION:
        return {"status": "unavailable", "code": "runtime_missing"}
    if request["device"] == "cuda" and not status.get("gpu", {}).get("available"):
        return {"status": "unavailable", "code": "cuda_unavailable"}
    targets = _cache_targets(worker)
    if request.get("cache_targets", targets) != targets:
        return {"status": "unavailable", "code": "cache_changed"}
    if request["mode"] == "inspect":
        return {"status": "ready", "cache_targets": targets}
    os.environ["NLTK_DATA"] = targets["nltk"]
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    # Existing prefetch passes token=False for every pinned public snapshot.
    caches = worker.prefetch(())
    if not all(caches.get("models", {}).get(key) is True for key in PUBLIC_MODELS) or (
        caches.get("punkt_tab") is not True
    ):
        return {"status": "failed", "code": "incomplete"}
    return {"status": "ready", "cache_targets": targets}


def _speakers(request):
    root = str(HERE.parent)
    sys.path.insert(0, root)
    import cbs_transcriber

    if Path(cbs_transcriber.__file__).resolve().parent != HERE:
        raise Refused("source")
    from cbs_transcriber import enhancement_api

    if Path(enhancement_api.__file__).resolve().parent != HERE:
        raise Refused("source")
    # Reuse the existing pinned-file, retry, corruption and completion-marker policy.
    enhancement_api.prepare_local_speakers(request["token"])
    return {"status": "ready", "speaker_model_local": "verified"}


def execute(value):
    request = _request(value)
    # Third-party progress/messages must never become protocol output or secret logs.
    with open(os.devnull, "w", encoding="utf-8") as sink:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            result = _speakers(request) if request["mode"] == "speakers" else _public(request)
    return {
        "protocol": PROTOCOL,
        "nonce": request["nonce"],
        "helper_sha256": request["helper_sha256"],
        "worker_sha256": request["worker_sha256"],
        **result,
    }


def main():
    nonce = None
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise Refused("input")
        value = json.loads(raw)
        if type(value) is dict and isinstance(value.get("nonce"), str):
            nonce = value["nonce"] if re.fullmatch(r"[a-f0-9]{32}", value["nonce"]) else None
        result = execute(value)
    except Refused as exc:
        result = {"protocol": PROTOCOL, "nonce": nonce, "status": "failed", "code": exc.code}
    except Exception:
        result = {"protocol": PROTOCOL, "nonce": nonce, "status": "failed", "code": "preparation"}
    raw = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_REPLY:
        raw = json.dumps({"protocol": PROTOCOL, "status": "failed", "code": "output"}).encode()
    sys.stdout.buffer.write(raw + b"\n")
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
