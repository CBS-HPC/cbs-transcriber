"""Warm passage processing without ML imports, model weights, or network access."""

import ctypes
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement, enhancement_worker
from cbs_transcriber.enhancement import EnhancementOptions
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.local_runtime import LocalPassageRuntime

SYNTHETIC_WORKER = r"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

assert sys.argv[1:] == ["--serve-passages"]

def write(folder, name, data):
    pending = folder / (name + ".pending")
    pending.write_text(json.dumps(data), encoding="utf-8")
    pending.replace(folder / name)

for line in sys.stdin:
    folder = Path(json.loads(line))
    request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
    child = None
    if request.get("block"):
        child = subprocess.Popen([
            sys.executable, "-I", "-c",
            "import os,sys,time; from pathlib import Path; "
            "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)",
            str(folder / "descendant.pid"),
        ])
        while not (folder / "descendant.pid").exists():
            time.sleep(0.01)
    write(folder, "started.json", {
        "pid": os.getpid(),
        "child_pid": int((folder / "descendant.pid").read_text()) if child else None,
        "environment": dict(os.environ),
    })
    write(folder, "progress.json", {"stage": "aligning"})
    # Tree termination may stop the child before its parent. That is not completed work.
    if child and child.wait():
        break
    write(folder, "alignment.json", {"passages": [{"id": "0", "words": []}]})
    write(folder, "done.json", {"complete": True})
"""


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("Synthetic subprocess did not reach the expected state.")
        time.sleep(0.01)


def request_folder(root, name, *, block=False):
    folder = root / name
    folder.mkdir()
    (folder / "request.json").write_text(json.dumps({"block": block}), encoding="utf-8")
    return folder


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    script = tmp_path / "synthetic_worker.py"
    script.write_text(SYNTHETIC_WORKER, encoding="utf-8")
    monkeypatch.setattr(enhancement, "WORKER", script)
    runtime = LocalPassageRuntime(idle_seconds=60)
    try:
        yield runtime
    finally:
        runtime.close()


def run(runtime, folder, options=None):
    runtime.run(folder, options or EnhancementOptions(), threading.Event(), lambda message: None)
    return json.loads((folder / "started.json").read_text(encoding="utf-8"))


def test_successive_passages_reuse_one_process_and_exclude_credentials(
    runtime, tmp_path, monkeypatch
):
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "OPENAI_API_KEY", "UCLOUD_API_TOKEN"):
        monkeypatch.setenv(name, "synthetic-secret")
    first = request_folder(tmp_path, "first")
    second = request_folder(tmp_path, "second")
    initial = run(runtime, first)
    process = runtime.process
    earlier_generation = runtime.generation
    repeated = run(runtime, second)
    runtime.expire(earlier_generation)
    # Windows' venv redirector may own a different PID from the interpreter it starts.
    assert repeated["pid"] == initial["pid"]
    assert runtime.process is process and process.poll() is None
    for folder in (first, second):
        assert json.loads((folder / "alignment.json").read_text())["passages"][0]["id"] == "0"
        assert json.loads((folder / "done.json").read_text())["complete"] is True
    environment = {key.upper(): value for key, value in initial["environment"].items()}
    assert "synthetic-secret" not in environment.values()
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
    assert environment["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert environment["DO_NOT_TRACK"] == "1"
    assert environment["PYANNOTE_METRICS_ENABLED"] == "0"


@pytest.mark.parametrize("changed", ["language", "device", "runtime_python"])
def test_changed_processing_identity_replaces_the_process(runtime, tmp_path, changed):
    options = EnhancementOptions()
    initial = run(runtime, request_folder(tmp_path, "first"), options)
    original = runtime.process
    if changed == "runtime_python":
        path = Path(sys.executable)
        value = str(path.parent / ".." / path.parent.name / path.name)
        assert Path(value).is_file() and value != options.runtime_python
    else:
        value = {"language": "en", "device": "cuda"}[changed]
    replacement = options.model_copy(update={changed: value})
    info = run(runtime, request_folder(tmp_path, "changed"), replacement)
    assert original.poll() is not None
    assert runtime.process is not original
    assert info["pid"] != initial["pid"]
    assert runtime.identity == (
        enhancement.python_path(replacement),
        replacement.language,
        replacement.device,
    )


def test_idle_expiry_releases_the_process_and_next_request_starts_clean(runtime, tmp_path):
    runtime.idle_seconds = 0.03
    run(runtime, request_folder(tmp_path, "first"))
    original = runtime.process
    wait_for(lambda: runtime.process is None)
    assert original.poll() is not None
    assert runtime.identity is None
    runtime.idle_seconds = 60
    run(runtime, request_folder(tmp_path, "after-idle"))
    assert runtime.process is not original and runtime.process.poll() is None


def process_handle(pid):
    if os.name != "nt":
        return pid
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x00100000, False, pid)
    assert handle, "Could not observe the synthetic worker child."
    return handle


def stopped(handle):
    if os.name == "nt":
        kernel = ctypes.windll.kernel32
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        return kernel.WaitForSingleObject(handle, 0) == 0
    try:
        os.kill(handle, 0)
    except ProcessLookupError:
        return True
    path = Path(f"/proc/{handle}/stat")
    return path.exists() and path.read_text().split()[2] == "Z"


@pytest.mark.parametrize("descendant_first", [False, True])
def test_cancellation_stops_the_worker_tree_and_next_request_starts_clean(
    runtime, tmp_path, monkeypatch, descendant_first
):
    folder = request_folder(tmp_path, "cancelled", block=True)
    cancel = threading.Event()
    errors = []

    def work():
        try:
            runtime.run(folder, EnhancementOptions(), cancel, lambda message: None)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    handle = worker_handle = None
    try:
        info = {}

        def started():
            # Windows may still hold the stub's file open; wait until it parses completely.
            try:
                info.update(json.loads((folder / "started.json").read_text()))
                return True
            except (OSError, ValueError):
                return False

        wait_for(started)
        original = runtime.process
        handle = process_handle(info["child_pid"])
        worker_handle = process_handle(info["pid"])
        if descendant_first:
            stop = enhancement.stop_process

            def stop_descendant_first(process):
                try:
                    if process is original and process.poll() is None:
                        os.kill(info["child_pid"], signal.SIGTERM)
                        wait_for(
                            lambda: (
                                process.poll() is not None or (folder / "alignment.json").exists()
                            )
                        )
                finally:
                    stop(process)

            monkeypatch.setattr(enhancement, "stop_process", stop_descendant_first)
        cancel.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], enhancement.media.Cancelled)
        assert original.poll() is not None
        wait_for(lambda: stopped(handle))
        wait_for(lambda: stopped(worker_handle))
        assert runtime.process is None and runtime.identity is None
        assert not (folder / "alignment.json").exists()
        run(runtime, request_folder(tmp_path, "after-cancel"))
        assert runtime.process is not original and runtime.process.poll() is None
    finally:
        cancel.set()
        thread.join(timeout=5)
        if os.name == "nt":
            ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            for observed in (handle, worker_handle):
                if observed:
                    ctypes.windll.kernel32.CloseHandle(observed)


def test_queue_shutdown_closes_an_idle_warm_runtime(runtime, tmp_path):
    run(runtime, request_folder(tmp_path, "first"))
    process = runtime.process
    queue = JobQueue(None, None)
    queue.workers[-1].passage_runtime = runtime
    queue.stop()
    assert runtime.process is None and process.poll() is not None
    assert all(worker.stopping.is_set() for worker in queue.workers)


def test_queue_shutdown_cancels_active_warm_processing_and_waits_for_exit(runtime, tmp_path):
    folder = request_folder(tmp_path, "active-shutdown", block=True)
    queue = JobQueue(None, None)
    owner = queue.workers[-1]
    owner.passage_runtime = runtime
    errors = []

    def work():
        try:
            runtime.run(folder, EnhancementOptions(), owner.cancel_event, lambda message: None)
        except BaseException as error:
            errors.append(error)

    owner.thread = threading.Thread(target=work, daemon=True)
    owner.thread.start()
    wait_for(lambda: (folder / "started.json").exists())
    process = runtime.process
    queue.stop()
    assert not owner.thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], enhancement.media.Cancelled)
    assert runtime.process is None and process.poll() is not None


def test_worker_reuses_alignment_model_only_for_the_same_language_and_device(tmp_path, monkeypatch):
    loads, alignments, snapshots = [], [], []

    def load_model(language, device, **kwargs):
        assert kwargs["model_cache_only"] is True
        model = object()
        loads.append((language, device, model))
        return model, {"language": language}

    def align(passages, model, metadata, audio, device, **kwargs):
        assert kwargs["interpolate_method"] == "ignore"
        assert kwargs["return_char_alignments"] is False
        alignments.append((model, metadata["language"], device))
        return {"word_segments": [{"word": "Hej", "start": 0, "end": 1, "score": 0.9}]}

    def snapshot(repo, **kwargs):
        assert kwargs["local_files_only"] is True
        assert kwargs["token"] is False
        assert len(kwargs["revision"]) == 40
        snapshots.append(repo)
        return "synthetic-cached-model"

    modules = {
        "torch": SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: True, empty_cache=lambda: None)
        ),
        "whisperx": SimpleNamespace(align=align, load_align_model=load_model),
        "huggingface_hub": SimpleNamespace(snapshot_download=snapshot),
        "nltk": SimpleNamespace(data=SimpleNamespace(load=lambda path: None)),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(enhancement_worker, "audio_data", lambda folder: [])
    monkeypatch.setattr(enhancement_worker, "CACHE_ALIGNMENT", True)
    monkeypatch.setattr(enhancement_worker, "ALIGNMENT_CACHE", None)
    combinations = [("da", "cpu"), ("da", "cpu"), ("en", "cpu"), ("en", "cuda")]
    for index, (language, device) in enumerate(combinations):
        folder = request_folder(tmp_path, f"passage-{index}")
        request = {
            "options": EnhancementOptions(language=language, device=device).model_dump()
            | {"duration": 1},
            "passages": [{"id": "0", "start": 0, "end": 1, "text": "Hej"}],
        }
        assert enhancement_worker.execute(request, folder) is None
        output = json.loads((folder / "alignment.json").read_text())
        assert output["passages"][0]["words"][0]["start"] == 0
    assert [(language, device) for language, device, _ in loads] == [
        ("da", "cpu"),
        ("en", "cpu"),
        ("en", "cuda"),
    ]
    assert alignments[0][0] is alignments[1][0]
    assert alignments[1][0] is not alignments[2][0]
    assert alignments[2][0] is not alignments[3][0]
    assert len(snapshots) == 3
