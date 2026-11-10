"""Offline source-install smoke check; synthetic media and a disposable library only."""

import argparse
import io
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import wave
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from importlib import metadata
from pathlib import Path

CORE = (
    "cbs-transcriber",
    "fastapi",
    "uvicorn",
    "python-multipart",
    "openai",
    "imageio-ffmpeg",
    "keyring",
    "platformdirs",
    "ucloud-workflow",
    "httpx",
    "websockets",
)
LOCAL = ("whisperx", "faster-whisper", "torch", "torchaudio", "torchvision")


class NoCredentials:
    def get(self, *args, **kwargs):
        raise RuntimeError("Credential access is forbidden in the installation check.")

    set = get


def package_status(name):
    try:
        value = metadata.version(name)
    except metadata.PackageNotFoundError:
        return {"installed": False, "version": None}
    except (OSError, ValueError, UnicodeError):
        return {"installed": None, "version": None}
    # Local build labels and arbitrary metadata never enter the shareable report.
    match = re.fullmatch(r"(\d+(?:\.\d+)*(?:(?:a|b|rc)\d+)?(?:\.post\d+)?)(?:\+[^\s]+)?", value)
    return {"installed": True, "version": match[1] if match else None}


def platform_status():
    system = platform.system()
    machine = platform.machine().lower()
    architecture = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine, "other")
    release = platform.mac_ver()[0] if system == "Darwin" else platform.release()
    release = release if re.fullmatch(r"\d+(?:\.\d+){0,3}", release) else None
    policy = "outside_acceptance_targets"
    if system == "Windows" and architecture == "x86_64":
        policy = "cpu_local_packages_expected"
    elif system == "Darwin" and architecture == "x86_64":
        policy = "intel_mac_core_only"
    elif system == "Darwin" and architecture == "arm64":
        policy = (
            "cpu_local_packages_expected"
            if release and int(release.split(".")[0]) >= 14
            else "macos_14_or_later_required"
        )
    return {
        "system": system if system in {"Windows", "Darwin", "Linux"} else "other",
        "release": release,
        "process_architecture": architecture,
        "local_package_policy": policy,
    }


@contextmanager
def isolated(directory):
    settings = {
        "CBS_TRANSCRIBER_DATA_DIR": None,
        "CBS_TRANSCRIBER_MODELS": None,
        "IMAGEIO_FFMPEG_EXE": None,
        "CBS_TRANSCRIBER_NO_STARTUP_CHECK": "1",
        "CBS_TRANSCRIBER_NO_REMOTE_RELEASE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
    }
    previous = {key: os.environ.get(key) for key in settings}
    old_temp, old_bytecode = tempfile.tempdir, sys.dont_write_bytecode
    tempfile.tempdir, sys.dont_write_bytecode = str(directory), True
    try:
        for key, value in settings.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        tempfile.tempdir, sys.dont_write_bytecode = old_temp, old_bytecode
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def bundled_ffmpeg():
    import imageio_ffmpeg
    from imageio_ffmpeg._definitions import FNAME_PER_PLATFORM, get_platform

    bundled = Path(imageio_ffmpeg.__file__).resolve().parent / "binaries"
    filename = FNAME_PER_PLATFORM.get(get_platform())
    if filename is None:
        raise RuntimeError("This package has no bundled FFmpeg candidate for the platform.")
    executable = (bundled / filename).resolve()
    # The pinned package definitions only select a path. Its locator executes
    # unbounded probes and falls back to conda/PATH, so never call it here.
    if not executable.is_relative_to(bundled) or not executable.is_file():
        raise RuntimeError("The bundled FFmpeg executable is unavailable.")
    result = subprocess.run(
        [str(executable), "-version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise RuntimeError("The bundled FFmpeg executable failed.")
    match = re.match(rb"ffmpeg version (\d+(?:\.\d+)+)", result.stdout)
    os.environ["IMAGEIO_FFMPEG_EXE"] = str(executable)
    return {"status": "pass", "source": "bundled", "version": match[1].decode() if match else None}


def require(condition):
    if not condition:
        raise RuntimeError("The synthetic core check failed.")


def core_smoke(directory, checks):
    from fastapi.testclient import TestClient

    from cbs_transcriber.app import create_app

    app = create_app(directory / "library", start_worker=False, credentials=NoCredentials())
    with TestClient(app, base_url="http://127.0.0.1", headers={"X-CBS-Request": "1"}) as client:
        require(client.get("/api/health").status_code == 200)
        checks["core_startup"] = "pass"
        project = client.post("/api/projects", json={"name": "Synthetic installation check"})
        require(project.status_code == 201)
        audio = io.BytesIO()
        with wave.open(audio, "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x00\x00" * 16000)
        uploaded = client.post(
            "/api/recordings",
            data={"project_id": project.json()["id"]},
            files={"file": ("synthetic.wav", audio.getvalue(), "audio/wav")},
        )
        require(uploaded.status_code == 201)
        job = uploaded.json()["job"]
        store, queue = app.state.store, app.state.queue
        store.update_job(job["id"], state="running")
        timeout = threading.Timer(30, queue.cancel_event.set)
        timeout.start()
        try:
            queue.process(job["id"])
        finally:
            timeout.cancel()
        store.update_job(job["id"], state="completed", progress=1)
        recording = store.recording(job["recording_id"])
        require(recording["status"] == "ready")
        require(not any(worker.thread for worker in queue.workers))
        checks["media_preparation"] = "pass"
        literal = "Synthetic æøå installation check."
        imported = client.post(
            f"/api/recordings/{recording['id']}/transcript",
            data={"revision": recording["revision"]},
            files={
                "file": (
                    "synthetic.json",
                    json.dumps({"segments": [{"text": literal, "start": 0, "end": 1}]}).encode(),
                    "application/json",
                )
            },
        )
        require(imported.status_code == 200)
        exported = client.get(f"/api/recordings/{recording['id']}/export")
        require(exported.status_code == 200 and literal in exported.text)
        checks["transcript_import_export"] = "pass"
        expected = (store.directory(recording["id"]) / "audio.mp3").read_bytes()
        response = client.get(
            f"/api/recordings/{recording['id']}/media/audio", headers={"Range": "bytes=0-99"}
        )
        require(response.status_code == 206 and response.content == expected[:100])
        require(len(response.content) == 100)
        require(response.headers["accept-ranges"] == "bytes")
        require(response.headers["content-range"] == f"bytes 0-99/{len(expected)}")
        checks["playback_byte_range"] = "pass"


def run_checks(*, temp_parent=None):
    report = {
        "format": "cbs-installation-smoke-v1",
        "platform": platform_status(),
        "python": {
            "version": ".".join(map(str, sys.version_info[:3])),
            "implementation": sys.implementation.name
            if sys.implementation.name in {"cpython", "pypy"}
            else "other",
            "supported": sys.version_info[:2] == (3, 13),
        },
        "packages": {name: package_status(name) for name in (*CORE, *LOCAL)},
        "local_inference": "not_tested",
        "gpu": "not_checked",
        "background_scheduling": "not_tested",
        "browser_playback": "not_tested",
        "credential_store": "not_accessed",
        "real_device_acceptance": "not_established",
        "ffmpeg": {"status": "not_run"},
        "checks": dict.fromkeys(
            (
                "core_startup",
                "media_preparation",
                "transcript_import_export",
                "playback_byte_range",
            ),
            "not_run",
        ),
        "error": None,
    }
    stage = "ffmpeg"
    try:
        with tempfile.TemporaryDirectory(prefix="cbs-install-check-", dir=temp_parent) as temporary:
            with (
                isolated(Path(temporary)),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                report["ffmpeg"] = bundled_ffmpeg()
                stage = "core_smoke"
                core_smoke(Path(temporary), report["checks"])
    except Exception:
        report["error"] = stage + "_failed"
        if stage == "ffmpeg":
            report["ffmpeg"] = {"status": "fail"}
    report["core_status"] = (
        "pass"
        if (
            report["error"] is None
            and report["python"]["supported"]
            and all(report["packages"][name]["installed"] for name in CORE)
            and all(value == "pass" for value in report["checks"].values())
        )
        else "fail"
    )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", type=Path, help="Create a new UTF-8 JSON report; never overwrite."
    )
    args = parser.parse_args(argv)
    try:
        with ExitStack() as outputs:
            stream = (
                outputs.enter_context(args.report.open("x", encoding="utf-8", newline="\n"))
                if args.report
                else None
            )
            try:
                report = run_checks()
            except Exception:
                report = {
                    "format": "cbs-installation-smoke-v1",
                    "core_status": "fail",
                    "error": "report_collection_failed",
                }
            raw = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            if stream:
                stream.write(raw)
            print(raw, end="")
            return 0 if report["core_status"] == "pass" else 1
    except OSError:
        print("The report could not be created. Choose a new writable file.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    # Source clones and ZIPs both use this checkout, without a Git invocation.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    raise SystemExit(main())
